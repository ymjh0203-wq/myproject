"""앱에서 자주 바꾸는 등록 설정을 JSON으로 저장한다."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Iterable


WATERMARK_POSITIONS = {
    "top_left",
    "top_right",
    "center",
    "bottom_left",
    "bottom_right",
}


def _clean_paths(values: Iterable[str] | None) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        path = str(value).strip()
        if path and path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def fixed_page_defaults(
    *, default_top: Path, default_bottom: Path
) -> dict[str, list[str]]:
    return {
        "top_assets": [str(default_top)] if default_top.is_file() else [],
        "bottom_assets": [str(default_bottom)] if default_bottom.is_file() else [],
    }


def load_fixed_page_settings(
    source: Path,
    *,
    default_top: Path,
    default_bottom: Path,
) -> dict[str, list[str]]:
    defaults = fixed_page_defaults(
        default_top=default_top,
        default_bottom=default_bottom,
    )
    if not source.is_file():
        return defaults
    with source.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("고정페이지 설정 파일 형식이 올바르지 않습니다.")
    return {
        "top_assets": _clean_paths(payload.get("top_assets"))
        or defaults["top_assets"],
        "bottom_assets": _clean_paths(payload.get("bottom_assets"))
        or defaults["bottom_assets"],
    }


def save_fixed_page_settings(
    destination: Path,
    *,
    top_assets: Iterable[str],
    bottom_assets: Iterable[str],
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "top_assets": _clean_paths(top_assets),
        "bottom_assets": _clean_paths(bottom_assets),
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as temp_file:
        json.dump(payload, temp_file, ensure_ascii=False, indent=2)
        temp_path = Path(temp_file.name)
    temp_path.replace(destination)
    return destination


def watermark_defaults() -> dict:
    return {
        "enabled": False,
        "image_path": "",
        "text": "",
        "position": "bottom_right",
        "opacity_percent": 25,
        "size_percent": 15,
        "angle_degrees": 0,
    }


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, number))


def load_watermark_settings(source: Path) -> dict:
    settings = watermark_defaults()
    if not source.is_file():
        return settings
    with source.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("워터마크 설정 파일 형식이 올바르지 않습니다.")
    position = str(payload.get("position") or settings["position"])
    settings.update(
        {
            "enabled": bool(payload.get("enabled", settings["enabled"])),
            "image_path": str(payload.get("image_path") or "").strip(),
            "text": str(payload.get("text") or "").strip(),
            "position": (
                position if position in WATERMARK_POSITIONS else settings["position"]
            ),
            "opacity_percent": _bounded_int(
                payload.get("opacity_percent"),
                default=settings["opacity_percent"],
                minimum=5,
                maximum=100,
            ),
            "size_percent": _bounded_int(
                payload.get("size_percent"),
                default=settings["size_percent"],
                minimum=5,
                maximum=50,
            ),
            "angle_degrees": _bounded_int(
                payload.get("angle_degrees"),
                default=settings["angle_degrees"],
                minimum=-90,
                maximum=90,
            ),
        }
    )
    return settings


def save_watermark_settings(
    destination: Path,
    *,
    enabled: bool,
    image_path: str,
    text: str,
    position: str,
    opacity_percent: int,
    size_percent: int,
    angle_degrees: int,
) -> Path:
    defaults = watermark_defaults()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "enabled": bool(enabled),
        "image_path": str(image_path or "").strip(),
        "text": str(text or "").strip(),
        "position": position if position in WATERMARK_POSITIONS else defaults["position"],
        "opacity_percent": _bounded_int(
            opacity_percent,
            default=defaults["opacity_percent"],
            minimum=5,
            maximum=100,
        ),
        "size_percent": _bounded_int(
            size_percent,
            default=defaults["size_percent"],
            minimum=5,
            maximum=50,
        ),
        "angle_degrees": _bounded_int(
            angle_degrees,
            default=defaults["angle_degrees"],
            minimum=-90,
            maximum=90,
        ),
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as temp_file:
        json.dump(payload, temp_file, ensure_ascii=False, indent=2)
        temp_path = Path(temp_file.name)
    temp_path.replace(destination)
    return destination
