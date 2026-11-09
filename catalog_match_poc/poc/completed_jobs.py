"""완료된 일괄등록 작업을 기록으로 남기고 활성 목록에서 제거한다."""

from __future__ import annotations

from pathlib import Path

from .batch_jobs import (
    STATUS_DRAFT_SAVED,
    STATUS_SUCCEEDED,
    BatchJob,
    BatchQueue,
)
from .report_store import save_report


COMPLETED_STATUSES = {STATUS_DRAFT_SAVED, STATUS_SUCCEEDED}


def archive_completed_job(job: BatchJob, output_dir: Path) -> Path:
    """완료 결과를 별도 보고서로 보관한다."""

    return save_report(
        {
            "report_type": "batch_completion",
            "product": {
                "product_id": job.product_id or "",
                "registered_product_id": job.registered_product_id,
            },
            "completion": job.to_dict(),
        },
        output_dir,
    )


def archive_and_remove_completed(
    queue: BatchQueue,
    output_dir: Path,
    *,
    job_ids: set[str] | None = None,
) -> list[Path]:
    """완료 작업만 기록한 뒤 활성 작업 목록에서 제거한다."""

    targets = [
        job
        for job in list(queue.jobs)
        if job.status in COMPLETED_STATUSES
        and (job_ids is None or job.job_id in job_ids)
    ]
    archived: list[Path] = []
    for job in targets:
        archived.append(archive_completed_job(job, output_dir))
        queue.remove(job.job_id)
    return archived
