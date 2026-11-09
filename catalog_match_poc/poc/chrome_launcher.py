"""설치된 Google Chrome을 기존 사용자 프로필 그대로 연다."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Callable, Mapping


class ChromeLaunchError(RuntimeError):
    """Chrome을 찾거나 실행하지 못했을 때 발생한다."""


class ChromeNotFoundError(ChromeLaunchError):
    pass


def chrome_candidates(environ: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    values = os.environ if environ is None else environ
    roots = (
        values.get("PROGRAMFILES"),
        values.get("PROGRAMFILES(X86)"),
        values.get("LOCALAPPDATA"),
    )
    return tuple(
        Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
        for root in roots
        if root
    )


def find_chrome(environ: Mapping[str, str] | None = None) -> Path | None:
    return next((path for path in chrome_candidates(environ) if path.is_file()), None)


def launch_chrome(
    url: str,
    *,
    executable: Path | None = None,
    popen: Callable[..., object] = subprocess.Popen,
) -> Path:
    """별도 프로필이나 쿠키 복사 없이 평소 사용하던 Chrome에 URL을 연다."""

    chrome = executable or find_chrome()
    if chrome is None:
        raise ChromeNotFoundError(
            "Google Chrome을 찾지 못했습니다. Chrome 설치 위치를 확인하세요."
        )
    try:
        popen([str(chrome), url], close_fds=True)
    except OSError as exc:
        raise ChromeLaunchError(
            "Google Chrome을 실행하지 못했습니다. Chrome을 완전히 종료한 뒤 "
            "프로그램에서 다시 시도하세요."
        ) from exc
    return chrome
