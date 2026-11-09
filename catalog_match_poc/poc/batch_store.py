"""일괄등록 작업 목록을 원자적으로 저장하고 불러온다."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time

from .batch_jobs import BatchQueue
from .report_store import sanitize_payload


def save_batch_queue(queue: BatchQueue, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = sanitize_payload(queue.to_dict())
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as temp_file:
        json.dump(payload, temp_file, ensure_ascii=False, indent=2)
        temp_path = Path(temp_file.name)
    # GUI 새로고침과 자동등록 완료가 거의 동시에 저장되면 Windows가 대상
    # 파일을 수십 ms 잠글 수 있다. 임시파일은 유지한 채 잠금이 풀릴 때까지
    # 짧게 재시도해 성공 행 자동 제거가 실패하지 않도록 한다.
    deadline = time.monotonic() + 3
    while True:
        try:
            os.replace(temp_path, destination)
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    return destination


def load_batch_queue(source: Path) -> BatchQueue:
    if not source.is_file():
        return BatchQueue()
    with source.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("일괄등록 작업 파일 형식이 올바르지 않습니다.")
    return BatchQueue.from_dict(payload)
