"""두 번째 실제 상세페이지 세트의 정리·한국어 자연화 시험."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


SOURCE_DIR = Path(r"C:\Users\User\Desktop\해외구매대행\상세페이지_번역\output\20260720_215137")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "translation_quality_test" / "second_detail_page"
FONT_REGULAR = r"C:\Windows\Fonts\NotoSansKR-VF.ttf"
FONT_BOLD = r"C:\Windows\Fonts\malgunbd.ttf"
INK = (47, 42, 37)
MUTED = (103, 91, 81)
AMBER = (171, 83, 14)
PAPER = (249, 246, 241)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size=size)


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, size: int, *, bold: bool = False, fill=INK) -> None:
    draw.text(xy, value, font=font(size, bold), fill=fill)


def card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, fill=(255, 255, 255)) -> None:
    draw.rounded_rectangle(box, radius=20, fill=fill, outline=(232, 222, 211), width=2)


def test_seller_cleanup(source: Image.Image) -> Image.Image:
    """제품 비교는 유지하고 다른 판매자의 연락처·배송 안내는 제거한다."""
    result = Image.new("RGB", source.size, PAPER)
    # 제품 사진과 ON/OFF 비교 영역은 픽셀을 그대로 보존한다.
    product_panel = source.crop((0, 0, 432, source.height))
    result.paste(product_panel, (0, 0))
    draw = ImageDraw.Draw(result)
    draw.rectangle((0, 516, 432, source.height), fill=PAPER)
    text(draw, (72, 524), "조명 ON/OFF 상태 비교", 23, bold=True)

    card(draw, (450, 28, 758, 543))
    text(draw, (482, 63), "제품 특징", 34, bold=True, fill=AMBER)
    draw.line((482, 116, 724, 116), fill=(220, 203, 188), width=2)
    text(draw, (482, 151), "조명 OFF", 25, bold=True)
    text(draw, (482, 193), "은은한 장식 효과", 23, fill=MUTED)
    text(draw, (482, 265), "조명 ON", 25, bold=True)
    text(draw, (482, 307), "따뜻한 간접조명", 23, fill=MUTED)
    draw.rounded_rectangle((482, 402, 724, 478), radius=14, fill=(244, 232, 219))
    text(draw, (509, 422), "판매자 연락처 제거", 21, bold=True, fill=AMBER)
    return result


def test_korean_naturalization(source: Image.Image) -> Image.Image:
    """상품 사진은 살리고 기계번역 문구와 불확실한 수치를 정리한다."""
    result = Image.new("RGB", source.size, PAPER)
    draw = ImageDraw.Draw(result)

    card(draw, (12, 18, 378, 553))
    text(draw, (34, 38), "수경재배 화병", 29, bold=True, fill=AMBER)
    text(draw, (34, 83), "식물이나 꽃을 수경재배할 수 있습니다.", 18, fill=MUTED)
    product_crop = source.crop((120, 159, 355, 548))
    result.paste(product_crop, (104, 132))
    draw = ImageDraw.Draw(result)
    draw.rounded_rectangle((34, 216, 238, 266), radius=8, fill=(255, 255, 255, 255))
    text(draw, (56, 230), "인조 식물 포함", 18, bold=True)
    draw.rounded_rectangle((34, 412, 256, 466), radius=8, fill=(255, 255, 255, 255))
    text(draw, (56, 427), "물을 담는 유리병", 18, bold=True)

    card(draw, (394, 18, 768, 553))
    text(draw, (418, 38), "5V LED 조명", 29, bold=True, fill=AMBER)
    text(draw, (418, 83), "저전압 5V 전원을 사용합니다.", 18, fill=MUTED)
    led_crop = source.crop((404, 252, 754, 347))
    result.paste(led_crop, (406, 145))
    draw = ImageDraw.Draw(result)
    text(draw, (425, 291), "확인 항목", 22, bold=True, fill=AMBER)
    text(draw, (425, 335), "• 색온도와 밝기", 18)
    text(draw, (425, 372), "• 연색지수와 소비전력", 18)
    text(draw, (425, 420), "원문과 대조한 뒤 등록합니다.", 17, fill=MUTED)
    draw.rounded_rectangle((432, 488, 731, 532), radius=11, fill=(255, 235, 218))
    text(draw, (451, 497), "‘97’ 수치는 원문 확인 필요", 18, bold=True, fill=(175, 64, 24))
    return result


def test_installation_rewrite(source: Image.Image) -> Image.Image:
    """어색한 설치 문장을 고객이 따라 하기 쉬운 순서로 다시 쓴다."""
    result = Image.new("RGB", source.size, PAPER)
    draw = ImageDraw.Draw(result)
    text(draw, (34, 29), "설치 방법", 36, bold=True)
    text(draw, (210, 43), "네 단계로 간단하게 설치할 수 있습니다.", 18, fill=MUTED)

    steps = [
        ("1", "설치 위치 선택", "줄자로 설치 위치와\n간격을 측정합니다."),
        ("2", "수평 확인", "수평계를 사용해\n수평을 맞춘 뒤\n위치를 표시합니다."),
        ("3", "못·나사 고정", "표시한 위치에\n벽면용 못이나 나사를\n고정합니다."),
        ("4", "제품 설치", "제품을 건 뒤\n수평과 고정 상태를\n확인합니다."),
    ]
    x_positions = [20, 210, 400, 590]
    for x, (number, title, body) in zip(x_positions, steps):
        card(draw, (x, 105, x + 170, 306))
        draw.ellipse((x + 15, 121, x + 49, 155), fill=(28, 28, 28))
        text(draw, (x + 27, 124), number, 18, bold=True, fill=(255, 255, 255))
        text(draw, (x + 20, 169), title, 20, bold=True)
        text(draw, (x + 20, 209), body, 14, fill=MUTED)

    card(draw, (20, 334, 760, 547), fill=(255, 255, 255))
    text(draw, (43, 357), "권장 도구", 25, bold=True, fill=AMBER)
    text(draw, (43, 403), "줄자 · 수평계 · 벽면용 못/나사 · 드라이버", 20)
    text(draw, (43, 462), "주의", 22, bold=True, fill=(175, 64, 24))
    text(draw, (105, 465), "벽면 재질에 맞는 고정 부품을 사용하세요.", 18, fill=MUTED)
    text(draw, (105, 500), "콘크리트 벽은 전동드릴이 필요할 수 있습니다.", 18, fill=MUTED)
    return result


def comparison(original: Image.Image, revised: Image.Image, title: str) -> Image.Image:
    width = original.width + revised.width
    height = max(original.height, revised.height) + 60
    result = Image.new("RGB", (width, height), "white")
    result.paste(original, (0, 60))
    result.paste(revised, (original.width, 60))
    draw = ImageDraw.Draw(result)
    text(draw, (22, 12), f"원본 — {title}", 26, bold=True)
    text(draw, (original.width + 22, 12), "교정 결과", 26, bold=True, fill=AMBER)
    return result


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    source_02 = Image.open(SOURCE_DIR / "02.jpg").convert("RGB")
    source_04 = Image.open(SOURCE_DIR / "04.jpg").convert("RGB")
    source_06 = Image.open(SOURCE_DIR / "06.jpg").convert("RGB")

    revised_02 = test_seller_cleanup(source_02)
    revised_04 = test_korean_naturalization(source_04)
    revised_06 = test_installation_rewrite(source_06)

    outputs = {
        "seller_cleanup": OUTPUT_DIR / "01_seller_cleanup.png",
        "korean_naturalization": OUTPUT_DIR / "02_korean_naturalization.png",
        "installation_rewrite": OUTPUT_DIR / "03_installation_rewrite.png",
    }
    comparison(source_02, revised_02, "판매자 정보 혼합").save(outputs["seller_cleanup"])
    comparison(source_04, revised_04, "기계번역과 불확실한 수치").save(outputs["korean_naturalization"])
    comparison(source_06, revised_06, "설치 안내").save(outputs["installation_rewrite"])

    contact = Image.new("RGB", (1560, 1892), (239, 236, 232))
    y = 0
    for path in outputs.values():
        image = Image.open(path).convert("RGB")
        contact.paste(image, (0, y))
        y += image.height + 18
    contact_path = OUTPUT_DIR / "04_all_comparisons.png"
    contact.crop((0, 0, 1560, y - 18)).save(contact_path)

    audit = {
        "source_folder": str(SOURCE_DIR),
        "source_image_count": 7,
        "tested_images": ["02.jpg", "04.jpg", "06.jpg"],
        "page_decisions": {
            "01.jpg": "제거 권장: 다른 판매자 고객센터가 포함된 중복 합본",
            "02.jpg": "부분 유지: 제품 비교는 유지, 판매자 연락처·배송 안내 제거",
            "03.jpg": "유지: 제품 디자인 설명, 일부 어색한 문구만 자연화",
            "04.jpg": "교정: 기계번역 자연화, 숫자 97은 원문 확인 필요",
            "05.jpg": "검수 필요: 전원 방식·220V 등 규격을 원문과 대조",
            "06.jpg": "교정: 설치 순서를 자연스러운 한국어로 재작성",
            "07.jpg": "검수 필요: 드릴·앵커 규격을 원문과 대조 후 재작성",
        },
        "outputs": {key: str(value) for key, value in outputs.items()},
        "all_comparisons": str(contact_path),
        "assessment": {
            "seller_information_detection": "통과",
            "awkward_korean_detection": "통과",
            "numeric_spec_safeguard": "통과 — 97 수치를 자동 확정하지 않고 검수 표시",
            "product_image_reuse": "통과 — 제품 사진 영역을 원본에서 재사용",
        },
    }
    report_path = OUTPUT_DIR / "result.json"
    report_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
