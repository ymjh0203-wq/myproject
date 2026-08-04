# ==========================================================
# 쿠팡 상세페이지 규격 변환 (image_resize.py)
# ----------------------------------------------------------
# 쿠팡 상세페이지 이미지 규격(2026년 기준, 쿠팡 Wing 이미지 가이드라인):
#   - 가로: 권장 780px (최대 1000px)
#   - 세로: 이미지 1장당 최대 3000px (넘으면 여러 장으로 나눠야 함)
#   - 형식: JPG / JPEG / PNG만 가능 (GIF 불가)
#   - 용량: 장당 5MB 이하
#
# 이 파일은 번역이 끝난 이미지 한 장을 받아서, 위 규격에 맞는 JPG 조각들로
# 나눠줍니다. 세로로 긴 이미지 한 장이 여러 장으로 쪼개질 수 있습니다.
# ==========================================================

import io

from PIL import Image

RECOMMENDED_WIDTH = 780
MAX_HEIGHT_PER_IMAGE = 3000
MAX_FILE_BYTES = 5 * 1024 * 1024


def _encode_jpeg_under_limit(image: Image.Image, max_bytes: int) -> bytes:
    """용량이 max_bytes를 넘지 않을 때까지 화질을 낮춰가며 JPG로 저장합니다."""
    quality = 90
    data = b""
    while quality >= 40:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality)
        data = buffer.getvalue()
        if len(data) <= max_bytes:
            return data
        quality -= 10
    return data  # 화질을 최대한 낮춰도 넘으면, 그래도 가장 작은 결과를 반환


def split_grid_image(image_bytes: bytes, rows: int, cols: int) -> list[bytes]:
    """이미지 한 장 안에 여러 장이 격자(행x열)로 합쳐져 있을 때, 원래대로 낱장 PNG들로
    나눠서 반환합니다. 왼쪽에서 오른쪽, 위에서 아래 순서로 반환합니다.

    챗지피티에 여러 장을 한꺼번에 번역해달라고 했을 때, 결과를 한 장짜리 격자 이미지로
    합쳐서 주는 경우가 있어서 그걸 원래 장 수대로 되돌리는 용도입니다.
    """
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    cell_width = image.width // cols
    cell_height = image.height // rows

    pieces = []
    for r in range(rows):
        for c in range(cols):
            left = c * cell_width
            top = r * cell_height
            right = image.width if c == cols - 1 else left + cell_width
            bottom = image.height if r == rows - 1 else top + cell_height
            cell = image.crop((left, top, right, bottom))
            buffer = io.BytesIO()
            cell.save(buffer, format="PNG")
            pieces.append(buffer.getvalue())
    return pieces


def resize_for_coupang(image_bytes: bytes, target_width: int = RECOMMENDED_WIDTH) -> list[bytes]:
    """이미지 바이트를 받아 쿠팡 규격(JPG, 가로 target_width, 세로 3000px 이하, 5MB 이하)
    조각들의 리스트로 반환합니다. 세로 길이가 짧으면 조각은 1개입니다.
    """
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    if image.width != target_width:
        ratio = target_width / image.width
        new_height = max(1, round(image.height * ratio))
        image = image.resize((target_width, new_height), Image.LANCZOS)

    chunks = []
    y = 0
    while y < image.height:
        chunk_height = min(MAX_HEIGHT_PER_IMAGE, image.height - y)
        chunk = image.crop((0, y, image.width, y + chunk_height))
        chunks.append(_encode_jpeg_under_limit(chunk, MAX_FILE_BYTES))
        y += chunk_height

    return chunks
