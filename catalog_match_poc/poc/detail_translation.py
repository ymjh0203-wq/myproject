"""상세페이지 문구를 확인해 자연스러운 한국어 이미지로 만드는 작업 패키지."""

from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import re
import shutil

from PIL import Image, ImageDraw, ImageFont


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
SAFE_JOB_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{4,80}$")
DEFAULT_IMAGES_PER_PRODUCT = 5
DEFAULT_RETRY_ALLOWANCE_PERCENT = 20
WATERMARK_POSITIONS = {
    "top_left",
    "top_right",
    "center",
    "bottom_left",
    "bottom_right",
}

TRANSLATION_MODE_LABELS = {
    "auto_ko": "문구 확인 후 한국어 자연화",
    "original": "원본 유지",
}

TRANSLATION_STATUS_LABELS = {
    "not_checked": "문구 확인 전",
    "not_required": "원본 사용",
    "request_ready": "한국어 이미지 요청 준비됨",
    "review_required": "한국어 이미지 검수 필요",
    "approved": "한국어 이미지 사용 승인",
    "failed": "한국어 이미지 생성 실패",
}


def build_localization_prompt(source_names: list[str]) -> str:
    joined = ", ".join(source_names)
    return f"""Use case: text-localization
Asset type: 쿠팡 WING 상세페이지용 상품 이미지
Primary request: 각 입력 이미지의 문구 언어와 문장 품질을 먼저 확인하고, 고객이 읽기 편한 자연스러운 한국 쇼핑몰 문체의 한국어 이미지로 만든다.
Input images: {joined}
Text rules:
- 중국어(간체·번체)는 원문의 뜻을 정확히 보존해 자연스러운 한국어로 번역한다.
- 한국어가 기계 번역투이거나 어색하면 뜻을 바꾸지 않고 맞춤법, 띄어쓰기와 표현을 자연스럽게 교정한다.
- 이미 자연스러운 한국어 문구는 불필요하게 다시 쓰지 않고 그대로 유지한다.
- 중국어와 한국어가 섞인 이미지는 중국어를 번역하고 어색한 한국어만 교정한다.
- 한국어·중국어가 아닌 문구나 의미가 불확실한 문구는 추측하지 말고 검수 필요로 남긴다.
- 과장 광고나 원문에 없는 효능·인증·수치·혜택을 추가하지 않는다.
Constraints:
- 제품 사진, 배경, 아이콘, 색상, 테두리, 구성, 이미지 비율과 해상도를 유지한다.
- 숫자, 치수, 단위, 모델명, 옵션 번호와 고유명사는 원문과 대조해 정확히 유지한다.
- 글자가 없거나 수정할 문구가 없는 이미지는 손대지 않고 그대로 결과에 포함한다.
- 중국 판매자의 상호, 연락처, QR 코드, 메신저 ID, 자체 배송·교환 안내는 번역하지 말고 검수 대상으로 표시한다.
- 읽기 어려운 문구나 의미가 불확실한 문구를 추측하지 말고 검수 필요로 남긴다.
- 번역·교정 단계에서는 워터마크나 새 로고를 추가하지 않는다. 사용자가 설정한 자체
  워터마크는 번역 완료 후 프로그램의 별도 후처리 단계에서만 추가한다.
- 새 상품 주장이나 새 장식 요소를 추가하지 않는다.
Output:
- 입력 순서와 파일 수를 유지해 한국어 교정·번역 이미지 파일을 만든다.
- 이미지마다 중국어 번역, 한국어 교정, 변경 없음 중 무엇을 적용했는지 검수 메모에 구분한다.
- 모든 결과는 사람이 미리보기 검수한 후에만 상품등록에 사용한다.
"""


def estimate_localization_workload(
    product_count: int,
    images_per_product: int = DEFAULT_IMAGES_PER_PRODUCT,
    retry_allowance_percent: int = DEFAULT_RETRY_ALLOWANCE_PERCENT,
) -> dict[str, int]:
    """하루 예정 상품 수를 기본·재작업 포함 이미지 수로 환산한다."""

    if product_count < 0:
        raise ValueError("상품 수는 0 이상이어야 합니다.")
    if images_per_product < 1:
        raise ValueError("상품당 이미지 수는 1장 이상이어야 합니다.")
    if retry_allowance_percent < 0:
        raise ValueError("재작업 여유율은 0 이상이어야 합니다.")
    base_images = product_count * images_per_product
    retry_images = math.ceil(base_images * retry_allowance_percent / 100)
    return {
        "product_count": product_count,
        "images_per_product": images_per_product,
        "base_images": base_images,
        "retry_allowance_percent": retry_allowance_percent,
        "retry_images": retry_images,
        "estimated_total_images": base_images + retry_images,
    }


def _validate_job_id(job_id: str) -> str:
    if not SAFE_JOB_ID_RE.fullmatch(job_id or ""):
        raise ValueError("안전하지 않은 번역 작업 ID입니다.")
    return job_id


def _validate_sources(source_paths: list[str]) -> list[Path]:
    sources: list[Path] = []
    for value in source_paths:
        path = Path(value)
        if not path.is_file():
            raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {path}")
        if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            raise ValueError(f"지원하지 않는 이미지 형식입니다: {path.suffix}")
        sources.append(path)
    if not sources:
        raise ValueError("번역할 상세페이지 이미지를 선택하세요.")
    return sources


def prepare_translation_job(
    *,
    job_id: str,
    source_paths: list[str],
    output_root: Path,
) -> dict:
    """원본을 보존한 채 Codex/ImageGen용 작업 폴더와 지시서를 만든다."""

    safe_job_id = _validate_job_id(job_id)
    sources = _validate_sources(source_paths)
    job_dir = output_root / safe_job_id
    source_dir = job_dir / "source"
    result_dir = job_dir / "translated"
    source_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)

    copied_paths: list[str] = []
    for index, source in enumerate(sources, start=1):
        destination = source_dir / f"detail_{index:03d}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        copied_paths.append(str(destination))

    prompt = build_localization_prompt([Path(path).name for path in copied_paths])
    manifest = {
        "schema_version": 2,
        "job_id": safe_job_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "request_ready",
        "source_images": copied_paths,
        "translated_output_dir": str(result_dir),
        "prompt_file": str(job_dir / "translation_prompt.txt"),
        "requirements": {
            "preserve_product_photos": True,
            "preserve_numbers_units_models": True,
            "detect_language_per_image": True,
            "translate_chinese_to_korean": True,
            "rewrite_awkward_korean": True,
            "preserve_natural_korean": True,
            "keep_no_text_images_unchanged": True,
            "human_review_required": True,
        },
    }
    (job_dir / "translation_prompt.txt").write_text(prompt, encoding="utf-8")
    (job_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def import_translation_results(
    *,
    job_id: str,
    result_paths: list[str],
    output_root: Path,
) -> list[str]:
    """생성된 번역 이미지를 작업 폴더에 순서대로 복사한다."""

    safe_job_id = _validate_job_id(job_id)
    results = _validate_sources(result_paths)
    result_dir = output_root / safe_job_id / "translated"
    result_dir.mkdir(parents=True, exist_ok=True)
    imported: list[str] = []
    for index, source in enumerate(results, start=1):
        destination = result_dir / f"translated_{index:03d}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        imported.append(str(destination))
    return imported


def prepare_option_images(
    *,
    job_id: str,
    source_paths: list[str],
    output_root: Path,
) -> list[str]:
    """옵션 이미지 원본을 상품별 작업 폴더에 순서대로 복사한다."""

    safe_job_id = _validate_job_id(job_id)
    sources = _validate_sources(source_paths)
    source_dir = output_root / safe_job_id / "option_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for index, source in enumerate(sources, start=1):
        destination = source_dir / f"option_{index:03d}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        copied.append(str(destination))
    return copied


def delete_translation_job(*, job_id: str, output_root: Path) -> bool:
    """확인된 상품 작업 폴더만 삭제하고 상위 폴더로 벗어나는 경로는 거부한다."""

    safe_job_id = _validate_job_id(job_id)
    root = output_root.resolve()
    target = (root / safe_job_id).resolve()
    if target.parent != root:
        raise ValueError("상품 작업 폴더 경로가 안전하지 않습니다.")
    if not target.exists():
        return False
    if not target.is_dir():
        raise ValueError("상품 작업 경로가 폴더가 아닙니다.")
    shutil.rmtree(target)
    return True


def _watermark_position(
    *,
    canvas_size: tuple[int, int],
    mark_size: tuple[int, int],
    position: str,
) -> tuple[int, int]:
    canvas_width, canvas_height = canvas_size
    mark_width, mark_height = mark_size
    margin = max(8, round(min(canvas_width, canvas_height) * 0.02))
    positions = {
        "top_left": (margin, margin),
        "top_right": (canvas_width - mark_width - margin, margin),
        "center": (
            (canvas_width - mark_width) // 2,
            (canvas_height - mark_height) // 2,
        ),
        "bottom_left": (margin, canvas_height - mark_height - margin),
        "bottom_right": (
            canvas_width - mark_width - margin,
            canvas_height - mark_height - margin,
        ),
    }
    return positions.get(position, positions["bottom_right"])


def _text_watermark(text: str, *, target_width: int, opacity: int) -> Image.Image:
    font_path = Path(r"C:\Windows\Fonts\malgunbd.ttf")
    initial_size = max(18, target_width // max(2, len(text)))
    selected_font = (
        ImageFont.truetype(str(font_path), size=initial_size)
        if font_path.is_file()
        else ImageFont.load_default()
    )
    probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    probe_draw = ImageDraw.Draw(probe)
    left, top, right, bottom = probe_draw.textbbox(
        (0, 0), text, font=selected_font, stroke_width=1
    )
    measured_width = max(1, right - left)
    if measured_width > target_width and font_path.is_file():
        fitted_size = max(12, round(initial_size * target_width / measured_width))
        selected_font = ImageFont.truetype(str(font_path), size=fitted_size)
        left, top, right, bottom = probe_draw.textbbox(
            (0, 0), text, font=selected_font, stroke_width=1
        )
    width = max(1, right - left + 12)
    height = max(1, bottom - top + 12)
    mark = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mark)
    alpha = round(255 * opacity / 100)
    shadow_alpha = min(210, alpha)
    draw.text(
        (6 - left, 6 - top),
        text,
        font=selected_font,
        fill=(255, 255, 255, alpha),
        stroke_width=2,
        stroke_fill=(0, 0, 0, shadow_alpha),
    )
    return mark


def apply_watermark_to_images(
    *,
    source_paths: list[str],
    output_dir: Path,
    image_path: str = "",
    text: str = "",
    position: str = "bottom_right",
    opacity_percent: int = 25,
    size_percent: int = 15,
    angle_degrees: int = 0,
) -> list[str]:
    """원본을 보존하고 복사본에 사용자 지정 이미지 또는 문구 워터마크를 넣는다."""

    sources = _validate_sources(source_paths)
    position = position if position in WATERMARK_POSITIONS else "bottom_right"
    opacity = min(100, max(5, int(opacity_percent)))
    size = min(50, max(5, int(size_percent)))
    angle = min(90, max(-90, int(angle_degrees)))
    watermark_path = Path(image_path) if str(image_path).strip() else None
    if watermark_path and not watermark_path.is_file():
        raise FileNotFoundError(f"워터마크 이미지 파일을 찾을 수 없습니다: {watermark_path}")
    if watermark_path and watermark_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError(f"지원하지 않는 워터마크 이미지 형식입니다: {watermark_path.suffix}")
    watermark_text = str(text or "").strip()
    if not watermark_path and not watermark_text:
        raise ValueError("워터마크 이미지 또는 문구를 입력하세요.")

    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for index, source in enumerate(sources, start=1):
        with Image.open(source) as opened:
            base = opened.convert("RGBA")
        target_width = max(1, round(base.width * size / 100))
        if watermark_path:
            with Image.open(watermark_path) as opened_mark:
                mark = opened_mark.convert("RGBA")
            scale = target_width / max(1, mark.width)
            target_height = max(1, round(mark.height * scale))
            max_height = max(1, round(base.height * 0.35))
            if target_height > max_height:
                scale = max_height / max(1, mark.height)
                target_width = max(1, round(mark.width * scale))
                target_height = max_height
            mark = mark.resize((target_width, target_height), Image.Resampling.LANCZOS)
            alpha = mark.getchannel("A").point(lambda value: round(value * opacity / 100))
            mark.putalpha(alpha)
        else:
            mark = _text_watermark(
                watermark_text,
                target_width=target_width,
                opacity=opacity,
            )
        if angle:
            mark = mark.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        overlay.alpha_composite(
            mark,
            _watermark_position(
                canvas_size=base.size,
                mark_size=mark.size,
                position=position,
            ),
        )
        finished = Image.alpha_composite(base, overlay)
        destination = output_dir / f"watermarked_{index:03d}{source.suffix.lower()}"
        suffix = destination.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            finished.convert("RGB").save(destination, quality=95, optimize=True)
        elif suffix == ".webp":
            finished.save(destination, quality=95, method=6)
        else:
            finished.save(destination, optimize=True)
        saved.append(str(destination))
    return saved
