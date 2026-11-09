"""검수에서 확인된 다른 판매자 고정페이지를 이미지 지문으로 제외한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from PIL import Image


DEFAULT_FINGERPRINTS_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "settings"
    / "seller_fixed_page_fingerprints.json"
)


def perceptual_hash(path: str | Path) -> str:
    """리사이즈·재압축에도 비교 가능한 64비트 dHash를 만든다."""

    with Image.open(path) as source:
        image = source.convert("L").resize((9, 8))
        getter = getattr(image, "get_flattened_data", image.getdata)
        pixels = list(getter())
    value = 0
    for row in range(8):
        for column in range(8):
            if pixels[row * 9 + column] > pixels[row * 9 + column + 1]:
                value |= 1 << (row * 8 + column)
    return f"{value:016x}"


def _distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def load_fingerprints(path: str | Path = DEFAULT_FINGERPRINTS_PATH) -> list[str]:
    target = Path(path)
    if not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    values = payload.get("fingerprints", []) if isinstance(payload, dict) else []
    return [str(value).lower() for value in values if len(str(value)) == 16]


def filter_known_seller_pages(
    paths: Iterable[str],
    *,
    fingerprint_path: str | Path = DEFAULT_FINGERPRINTS_PATH,
    maximum_distance: int = 6,
) -> tuple[list[str], list[str]]:
    """학습된 판매자 안내와 유사한 이미지만 보수적으로 분리한다."""

    fingerprints = load_fingerprints(fingerprint_path)
    kept: list[str] = []
    removed: list[str] = []
    for value in paths:
        path = str(value)
        try:
            current = perceptual_hash(path)
        except (OSError, ValueError):
            kept.append(path)
            continue
        if any(_distance(current, known) <= maximum_distance for known in fingerprints):
            removed.append(path)
        else:
            kept.append(path)
    return kept, removed
