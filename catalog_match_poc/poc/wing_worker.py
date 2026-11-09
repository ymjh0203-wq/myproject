"""GUI와 분리된 WING 자동입력 실행기."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
import traceback

from .batch_jobs import STATUS_AWAITING_CONFIRMATION, STATUS_IN_PROGRESS
from .batch_store import load_batch_queue, save_batch_queue
from .completed_jobs import archive_and_remove_completed
from .settings_store import (
    load_fixed_page_settings,
    load_watermark_settings,
)
from .wing_automation import (
    WingAutomationError,
    is_controlled_chrome_ready,
    launch_controlled_chrome,
    prepare_job_in_wing,
    submit_prepared_job,
)


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_QUEUE = BASE_DIR / "data" / "batches" / "current_batch.json"
DEFAULT_PROFILE = BASE_DIR / "data" / "wing_chrome_profile"
DEFAULT_WORK_ROOT = BASE_DIR / "data" / "wing_assets"
DEFAULT_LOG = BASE_DIR / "data" / "logs" / "wing_automation.log"
COMPLETED_REPORT_DIR = BASE_DIR / "data" / "reports" / "completed_jobs"
FIXED_SETTINGS = BASE_DIR / "data" / "settings" / "fixed_pages.json"
WATERMARK_SETTINGS = BASE_DIR / "data" / "settings" / "watermark.json"
DEFAULT_TOP = BASE_DIR / "assets" / "fixed_pages" / "livingred_top.png"
DEFAULT_BOTTOM = BASE_DIR / "assets" / "fixed_pages" / "livingred_bottom.png"


def _log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as file:
        file.write(f"[{timestamp}] {message}\n")


def _update(queue_path: Path, job_id: str, stage: str, message: str) -> None:
    queue = load_batch_queue(queue_path)
    job = queue.get(job_id)
    if job:
        job.automation_stage = stage
        job.automation_message = message
        if stage == "return_fee_adjusted":
            match = re.search(r"([0-9][0-9,]*)\s*원", message or "")
            if match:
                job.return_shipping_fee = int(match.group(1).replace(",", ""))
        job.touch()
        save_batch_queue(queue, queue_path)


def _prepare(args) -> int:
    queue_path = Path(args.queue)
    queue = load_batch_queue(queue_path)
    job = queue.get(args.job_id)
    if not job:
        raise WingAutomationError("작업 목록에서 선택 상품을 찾지 못했습니다.")
    if job.status not in {STATUS_IN_PROGRESS, STATUS_AWAITING_CONFIRMATION}:
        raise WingAutomationError("자동입력을 시작할 수 없는 상품 상태입니다.")

    fixed = load_fixed_page_settings(
        FIXED_SETTINGS,
        default_top=DEFAULT_TOP,
        default_bottom=DEFAULT_BOTTOM,
    )
    watermark = load_watermark_settings(WATERMARK_SETTINGS)

    def progress(stage: str, message: str) -> None:
        _log(Path(args.log), f"{job.job_id} {stage}: {message}")
        _update(queue_path, job.job_id, stage, message)

    result = prepare_job_in_wing(
        job.to_dict(),
        profile_dir=Path(args.profile),
        work_root=DEFAULT_WORK_ROOT / job.job_id,
        fixed_top_paths=fixed["top_assets"],
        fixed_bottom_paths=fixed["bottom_assets"],
        watermark_settings=watermark,
        progress=progress,
    )
    queue = load_batch_queue(queue_path)
    queue.mark_awaiting_confirmation(
        job.job_id,
        minimum_selling_price=result["minimum_selling_price"],
        country_of_origin=result["country_of_origin"],
        matched_options=result["matched_options"],
        message=result["message"],
    )
    selected = queue.get(job.job_id)
    if selected:
        selected.return_shipping_fee = result["return_shipping_fee"]
        selected.touch()
    save_batch_queue(queue, queue_path)
    return 0


def _submit(args) -> int:
    queue_path = Path(args.queue)
    queue = load_batch_queue(queue_path)
    job = queue.get(args.job_id)
    if not job or job.status != STATUS_AWAITING_CONFIRMATION:
        raise WingAutomationError("최종 실행 확인 대기 상품을 찾지 못했습니다.")

    def progress(stage: str, message: str) -> None:
        _log(Path(args.log), f"{job.job_id} {stage}: {message}")
        _update(queue_path, job.job_id, stage, message)

    def restore_prepared_form():
        nonlocal job
        progress(
            "restoring_prepared_form",
            "자동입력용 Chrome이 닫혀 상품 내용을 다시 준비하는 중입니다.",
        )
        fixed = load_fixed_page_settings(
            FIXED_SETTINGS,
            default_top=DEFAULT_TOP,
            default_bottom=DEFAULT_BOTTOM,
        )
        watermark = load_watermark_settings(WATERMARK_SETTINGS)
        prepared = prepare_job_in_wing(
            job.to_dict(),
            profile_dir=Path(args.profile),
            work_root=DEFAULT_WORK_ROOT / job.job_id,
            fixed_top_paths=fixed["top_assets"],
            fixed_bottom_paths=fixed["bottom_assets"],
            watermark_settings=watermark,
            progress=progress,
        )
        refreshed_queue = load_batch_queue(queue_path)
        refreshed = refreshed_queue.get(job.job_id)
        if not refreshed:
            raise WingAutomationError("다시 준비할 상품을 작업 목록에서 찾지 못했습니다.")
        refreshed.minimum_selling_price = prepared["minimum_selling_price"]
        refreshed.country_of_origin = prepared["country_of_origin"]
        refreshed.matched_options = prepared["matched_options"]
        refreshed.return_shipping_fee = prepared["return_shipping_fee"]
        refreshed.status = STATUS_AWAITING_CONFIRMATION
        refreshed.automation_stage = "awaiting_confirmation"
        refreshed.automation_message = prepared["message"]
        refreshed.last_error = ""
        refreshed.touch()
        save_batch_queue(refreshed_queue, queue_path)
        job = refreshed

    # 최종 확인을 기다리는 동안 사용자가 Chrome을 닫았더라도 같은 버튼을
    # 다시 누르면 상품 내용을 복구한 뒤 최종 등록까지 이어서 실행한다.
    required_option_error = any(
        token in (job.last_error or "")
        for token in ("필수 구매 옵션", "누락된 필수 옵션")
    )
    if not is_controlled_chrome_ready() or required_option_error:
        restore_prepared_form()

    try:
        result = submit_prepared_job(
            job.to_dict(), profile_dir=Path(args.profile), progress=progress
        )
    except WingAutomationError as exc:
        recoverable_tokens = (
            "버튼이 비활성화",
            "필수 구매 옵션",
            "누락된 필수 옵션",
            "NullPointerException",
        )
        if not any(token in str(exc) for token in recoverable_tokens):
            raise
        restore_prepared_form()
        result = submit_prepared_job(
            job.to_dict(), profile_dir=Path(args.profile), progress=progress
        )
    queue = load_batch_queue(queue_path)
    queue.mark_succeeded(
        job.job_id,
        registered_product_id=result.get("registered_product_id", ""),
        minimum_selling_price=job.minimum_selling_price,
        country_of_origin=job.country_of_origin,
    )
    selected = queue.get(job.job_id)
    if selected and result.get("return_shipping_fee") is not None:
        selected.return_shipping_fee = int(result["return_shipping_fee"])
        selected.touch()
    # WING 등록 성공 상태를 먼저 저장해 중간 종료 시 중복 등록을 막는다.
    save_batch_queue(queue, queue_path)
    try:
        archived = archive_and_remove_completed(
            queue,
            COMPLETED_REPORT_DIR,
            job_ids={job.job_id},
        )
        save_batch_queue(queue, queue_path)
        _log(
            Path(args.log),
            f"{job.job_id} auto_removed: 완료 기록 저장 후 작업 목록에서 자동 제거"
            f" ({archived[0] if archived else '기록 없음'})",
        )
    except OSError as exc:
        # 실제 WING 처리는 이미 완료됐으므로 성공 행을 남겨 중복 등록을 방지한다.
        _log(
            Path(args.log),
            f"{job.job_id} completion_archive_failed: {exc}",
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("prepare-browser", "prepare", "submit"), required=True)
    parser.add_argument("--job-id", default="")
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    args = parser.parse_args()
    try:
        if args.mode == "prepare-browser":
            launch_controlled_chrome(profile_dir=Path(args.profile))
            return 0
        if args.mode == "submit":
            return _submit(args)
        prepared = _prepare(args)
        if prepared:
            return prepared
        # 행에서 선택한 임시저장/상품등록 방식은 이미 사용자의 실행 지시다.
        # 준비 작업을 별도 프로세스로 끝내지 않고 같은 프로세스에서 최종
        # WING 버튼과 확인창까지 이어서 처리해 중간 대기 상태를 없앤다.
        return _submit(args)
    except Exception as exc:
        _log(Path(args.log), traceback.format_exc())
        if args.job_id:
            queue = load_batch_queue(Path(args.queue))
            job = queue.get(args.job_id)
            if job:
                if args.mode == "submit":
                    # 최종 등록 오류를 확인 대기 상태로 남기면 현재 상품이
                    # 대기열을 계속 점유해 다음 상품이 시작되지 않는다.
                    # 실패 행으로 명확히 남기고 GUI가 다음 등록 대기 상품을
                    # 자동으로 이어서 처리하게 한다.
                    queue.mark_failed(job.job_id, str(exc))
                else:
                    queue.mark_failed(job.job_id, str(exc))
                save_batch_queue(queue, Path(args.queue))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
