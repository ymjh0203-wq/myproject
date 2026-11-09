"""로컬 OCR과 고정형 이미지 재합성의 품질을 확인하는 작은 시험 도구."""

from __future__ import annotations

from difflib import SequenceMatcher
import json
from pathlib import Path
import re

from PIL import Image, ImageChops, ImageDraw, ImageFont
from rapidocr_onnxruntime import RapidOCR


SOURCE_LINES = [
    "这个链接里面有好多款，头发有植入头发，也有手绘头发，",
    "全胶身体，材质是混合硅胶，都是空心的。",
    "具体哪一款？你发给我。",
    "不同款式，结构不同。",
]

KOREAN_LINES = [
    "이 링크에는 여러 종류가 있습니다.",
    "머리카락은 실제로 심은 식모형과",
    "손으로 그린 페인팅형이 있습니다.",
    "전신은 혼합 실리콘 소재의 속이 빈 구조입니다.",
    "원하시는 모델의 사진을 보내 주세요.",
    "모델마다 구조가 다릅니다.",
]

PHOTO_CHINESE_LINES = ["久保田收割机通用座椅", "新款 · 含网兜"]
PHOTO_KOREAN_LINES = ["쿠보타 콤바인 공용 시트", "신형 · 수납망 포함"]


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


def gradient(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        t = y / max(1, height - 1)
        for x in range(width):
            s = x / max(1, width - 1)
            pixels[x, y] = (
                int(238 - 20 * t),
                int(246 - 24 * s),
                int(250 - 12 * t),
            )
    return image


def draw_card(lines: list[str], *, korean: bool) -> Image.Image:
    image = gradient(1000, 720)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle((55, 58, 945, 662), radius=34, fill=(255, 255, 255, 235))
    draw.rounded_rectangle((55, 58, 945, 165), radius=34, fill=(24, 126, 143, 255))
    draw.rectangle((55, 130, 945, 165), fill=(24, 126, 143, 255))

    if korean:
        title_font = font(r"C:\Windows\Fonts\NotoSansKR-VF.ttf", 46)
        body_font = font(r"C:\Windows\Fonts\NotoSansKR-VF.ttf", 34)
        title = "상품 옵션 및 소재 안내"
    else:
        title_font = font(r"C:\Windows\Fonts\msyhbd.ttc", 46)
        body_font = font(r"C:\Windows\Fonts\msyh.ttc", 35)
        title = "款式与材质说明"

    draw.text((88, 81), title, font=title_font, fill=(255, 255, 255, 255))
    y = 205
    for line in lines:
        draw.text((90, y), line, font=body_font, fill=(32, 44, 57, 255))
        y += 68 if korean else 82

    draw.rounded_rectangle((90, 590, 910, 623), radius=16, fill=(225, 241, 244, 255))
    footer_font = font(
        r"C:\Windows\Fonts\NotoSansKR-VF.ttf" if korean else r"C:\Windows\Fonts\msyh.ttc",
        21,
    )
    footer = "검수용 번역 이미지" if korean else "用于翻译质量测试的示例图"
    draw.text((110, 592), footer, font=footer_font, fill=(24, 126, 143, 255))
    return image


def normalize(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]", "", value)


def draw_photo_text(base: Image.Image, *, korean: bool) -> Image.Image:
    image = base.convert("RGB").copy()
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, image.width, 96), fill=(20, 132, 141))
    draw.rectangle((0, image.height - 72, image.width, image.height), fill=(20, 132, 141))
    if korean:
        lines = PHOTO_KOREAN_LINES
        top_font = font(r"C:\Windows\Fonts\NotoSansKR-VF.ttf", 48)
        bottom_font = font(r"C:\Windows\Fonts\NotoSansKR-VF.ttf", 40)
    else:
        lines = PHOTO_CHINESE_LINES
        top_font = font(r"C:\Windows\Fonts\msyhbd.ttc", 48)
        bottom_font = font(r"C:\Windows\Fonts\msyhbd.ttc", 40)
    draw.text((28, 17), lines[0], font=top_font, fill="white")
    draw.text((28, image.height - 61), lines[1], font=bottom_font, fill="white")
    return image


def unchanged_percent(base: Image.Image, edited: Image.Image) -> float:
    base = base.convert("RGB")
    edited = edited.convert("RGB")
    middle_base = base.crop((0, 97, base.width, base.height - 72))
    middle_edited = edited.crop((0, 97, edited.width, edited.height - 72))
    difference = ImageChops.difference(middle_base, middle_edited)
    changed_bbox = difference.getbbox()
    return 100.0 if changed_bbox is None else 0.0


def main() -> None:
    output_dir = Path(__file__).resolve().parents[1] / "translation_quality_test"
    output_dir.mkdir(parents=True, exist_ok=True)

    source_path = output_dir / "01_source_chinese.png"
    annotated_path = output_dir / "02_ocr_detected.png"
    translated_path = output_dir / "03_translated_korean.png"
    comparison_path = output_dir / "04_comparison.png"
    report_path = output_dir / "result.json"
    photo_source_path = output_dir / "05_photo_source_chinese.png"
    photo_translated_path = output_dir / "06_photo_translated_korean.png"
    photo_comparison_path = output_dir / "07_photo_comparison.png"

    source = draw_card(SOURCE_LINES, korean=False)
    source.save(source_path)

    engine = RapidOCR()
    results, _elapsed = engine(str(source_path))
    results = results or []
    detected_lines = [item[1] for item in results]
    body_detected_lines = [
        line
        for line in detected_lines
        if line not in {"款式与材质说明", "用于翻译质量测试的示例图"}
    ]
    expected = normalize("".join(SOURCE_LINES))
    detected = normalize("".join(body_detected_lines))
    match_ratio = SequenceMatcher(None, expected, detected).ratio()

    annotated = source.copy()
    annotation_draw = ImageDraw.Draw(annotated, "RGBA")
    for box, _text, score in results:
        points = [(int(point[0]), int(point[1])) for point in box]
        annotation_draw.line(points + [points[0]], fill=(230, 53, 76, 255), width=3)
        annotation_draw.rounded_rectangle(
            (points[0][0], max(0, points[0][1] - 24), points[0][0] + 72, points[0][1]),
            radius=4,
            fill=(230, 53, 76, 220),
        )
        annotation_draw.text(
            (points[0][0] + 4, max(0, points[0][1] - 23)),
            f"{score:.2f}",
            font=font(r"C:\Windows\Fonts\malgun.ttf", 15),
            fill=(255, 255, 255, 255),
        )
    annotated.save(annotated_path)

    translated = draw_card(KOREAN_LINES, korean=True)
    translated.save(translated_path)

    comparison = Image.new("RGB", (2000, 780), "white")
    comparison.paste(source, (0, 60))
    comparison.paste(translated, (1000, 60))
    label_draw = ImageDraw.Draw(comparison)
    label_font = font(r"C:\Windows\Fonts\NotoSansKR-VF.ttf", 31)
    label_draw.text((24, 10), "원본 중국어", font=label_font, fill=(30, 42, 54))
    label_draw.text((1024, 10), "자연스러운 한국어", font=label_font, fill=(30, 42, 54))
    comparison.save(comparison_path)

    project_root = Path(__file__).resolve().parents[1]
    photo_base = Image.open(project_root / ".tmp_registration" / "detail_editor" / "detail_02.jpg")
    photo_source = draw_photo_text(photo_base, korean=False)
    photo_translated = draw_photo_text(photo_base, korean=True)
    photo_source.save(photo_source_path)
    photo_translated.save(photo_translated_path)

    photo_results, _photo_elapsed = engine(str(photo_source_path))
    photo_results = photo_results or []
    photo_detected_lines = [item[1] for item in photo_results]
    photo_expected = normalize("".join(PHOTO_CHINESE_LINES))
    photo_detected = normalize("".join(photo_detected_lines))
    photo_match_ratio = SequenceMatcher(None, photo_expected, photo_detected).ratio()

    photo_comparison = Image.new("RGB", (1560, 840), "white")
    photo_comparison.paste(photo_source, (0, 60))
    photo_comparison.paste(photo_translated, (780, 60))
    photo_label_draw = ImageDraw.Draw(photo_comparison)
    photo_label_font = font(r"C:\Windows\Fonts\NotoSansKR-VF.ttf", 31)
    photo_label_draw.text((24, 10), "상품 사진 + 중국어", font=photo_label_font, fill=(30, 42, 54))
    photo_label_draw.text((804, 10), "상품 사진 + 한국어", font=photo_label_font, fill=(30, 42, 54))
    photo_comparison.save(photo_comparison_path)

    report = {
        "source": str(source_path),
        "annotated": str(annotated_path),
        "translated": str(translated_path),
        "comparison": str(comparison_path),
        "photo_source": str(photo_source_path),
        "photo_translated": str(photo_translated_path),
        "photo_comparison": str(photo_comparison_path),
        "expected_chinese": SOURCE_LINES,
        "detected_lines": detected_lines,
        "body_detected_lines": body_detected_lines,
        "ocr_character_match_percent": round(match_ratio * 100, 1),
        "photo_detected_lines": photo_detected_lines,
        "photo_ocr_character_match_percent": round(photo_match_ratio * 100, 1),
        "photo_pixels_preserved_outside_text_areas_percent": unchanged_percent(
            photo_base, photo_translated
        ),
        "natural_korean": KOREAN_LINES,
        "note": "번역 문장은 현재 대화 모델로 자연화했고 이미지는 로컬에서 고정형으로 재합성함",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
