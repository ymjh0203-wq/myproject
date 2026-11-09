"""Phase 0 수동 검증 결과를 민감정보 없이 JSON으로 저장한다."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import tempfile
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit


KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|cookie|authorization|access[_-]?token|refresh[_-]?token|secret)"
    r"\s*[:=]\s*([^\s,;]+)"
)
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,})?\b")


def redact_sensitive_text(value: str) -> str:
    value = KEY_VALUE_SECRET_RE.sub(lambda match: f"{match.group(1)}=[삭제됨]", value or "")
    return JWT_RE.sub("[토큰 삭제됨]", value)


def strip_url_query(value: str) -> str:
    """WING 화면 주소에서 쿼리/fragment/userinfo를 저장하지 않는다."""

    value = (value or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return "[잘못된 URL]"
    host = parts.hostname or ""
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def sanitize_payload(value):
    if isinstance(value, dict):
        return {str(key): sanitize_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def save_report(payload: dict, output_dir: Path) -> Path:
    """결과를 임시 파일에 먼저 쓴 뒤 교체하여 중간 손상을 막는다."""

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_payload = sanitize_payload(payload)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    product_id = str(payload.get("product", {}).get("product_id") or "unknown")
    unique_suffix = uuid4().hex[:8]
    destination = output_dir / f"phase0_{timestamp}_{unique_suffix}_{product_id}.json"

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".tmp", dir=output_dir, delete=False
    ) as temp_file:
        json.dump(safe_payload, temp_file, ensure_ascii=False, indent=2)
        temp_path = Path(temp_file.name)
    temp_path.replace(destination)
    return destination
