"""여러 쿠팡 상품을 순서대로 처리하기 위한 영속 작업 큐."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha256
import re
from typing import Iterable

from .registration_plan import (
    SHIPPING_FEE_LABELS,
    build_return_shipping_fee_policy,
)
from .detail_translation import (
    TRANSLATION_MODE_LABELS,
    TRANSLATION_STATUS_LABELS,
)
from .url_parser import parse_coupang_url


STATUS_WAITING_SHIPPING = "waiting_shipping"
STATUS_WAITING_SUBMISSION = "waiting_submission"
STATUS_WAITING_TRANSLATION = "waiting_translation"
STATUS_READY = "ready"
STATUS_IN_PROGRESS = "in_progress"
STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
STATUS_DRAFT_SAVED = "draft_saved"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

STATUS_LABELS = {
    STATUS_WAITING_SHIPPING: "배송비 선택 필요",
    STATUS_WAITING_SUBMISSION: "등록방식 선택 필요",
    STATUS_WAITING_TRANSLATION: "한국어 이미지 준비/검수 필요",
    STATUS_READY: "등록 대기",
    STATUS_IN_PROGRESS: "WING 자동입력 진행 중",
    STATUS_AWAITING_CONFIRMATION: "최종 실행 확인 대기",
    STATUS_DRAFT_SAVED: "임시저장 완료",
    STATUS_SUCCEEDED: "등록 완료",
    STATUS_FAILED: "실패",
    STATUS_SKIPPED: "제외",
}

ACTIVE_STATUSES = {
    STATUS_WAITING_SHIPPING,
    STATUS_WAITING_SUBMISSION,
    STATUS_WAITING_TRANSLATION,
    STATUS_READY,
    STATUS_IN_PROGRESS,
    STATUS_AWAITING_CONFIRMATION,
}

URL_RE = re.compile(r"https?://[^\s\]\[<>()]+", re.IGNORECASE)

SUBMISSION_MODE_LABELS = {
    "draft": "임시저장",
    "final": "상품등록",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def extract_urls(text: str) -> list[str]:
    """일반 텍스트와 Markdown 링크에서 URL을 입력 순서대로 추출한다."""

    candidates = URL_RE.findall(text or "")
    if not candidates:
        candidates = [line.strip() for line in (text or "").splitlines()]

    urls: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = candidate.strip().rstrip(".,;'")
        if value and value not in seen:
            urls.append(value)
            seen.add(value)
    return urls


@dataclass
class BatchJob:
    job_id: str
    source_url: str
    normalized_url: str
    product_id: str | None
    vendor_item_id: str | None
    item_id: str | None
    search_value: str | None
    is_selected: bool = True
    shipping_fee_type: str | None = None
    submission_mode: str | None = None
    translation_mode: str = "auto_ko"
    translation_status: str = "not_checked"
    source_detail_image_paths: list[str] = field(default_factory=list)
    translated_detail_image_paths: list[str] = field(default_factory=list)
    source_option_image_paths: list[str] = field(default_factory=list)
    watermarked_option_image_paths: list[str] = field(default_factory=list)
    translation_review_approved: bool = False
    translation_notes: str = ""
    status: str = STATUS_WAITING_SHIPPING
    attempts: int = 0
    minimum_selling_price: int | None = None
    country_of_origin: str = ""
    return_shipping_fee: int | None = None
    registered_product_id: str = ""
    last_error: str = ""
    automation_stage: str = ""
    automation_message: str = ""
    matched_options: list[dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_url(cls, source_url: str) -> "BatchJob":
        result = parse_coupang_url(source_url)
        normalized = result.normalized_url or source_url.strip()
        product_id = result.value_for("productId")
        vendor_item_id = result.value_for("vendorItemId")
        item_id = result.value_for("itemId")
        identity = product_id or vendor_item_id or item_id or normalized
        job_id = sha256(identity.encode("utf-8")).hexdigest()[:16]
        candidates = result.search_candidates()
        search_value = candidates[0].value if candidates else None
        valid = bool(result.is_coupang_url and search_value)
        timestamp = _now()
        return cls(
            job_id=job_id,
            source_url=source_url.strip(),
            normalized_url=normalized,
            product_id=product_id,
            vendor_item_id=vendor_item_id,
            item_id=item_id,
            search_value=search_value,
            status=STATUS_WAITING_SHIPPING if valid else STATUS_FAILED,
            last_error="" if valid else "유효한 쿠팡 상품 ID를 찾지 못했습니다.",
            created_at=timestamp,
            updated_at=timestamp,
        )

    @classmethod
    def from_dict(cls, value: dict) -> "BatchJob":
        allowed = cls.__dataclass_fields__.keys()
        data = {key: value.get(key) for key in allowed if key in value}
        return cls(**data)

    @property
    def shipping_fee_label(self) -> str:
        return SHIPPING_FEE_LABELS.get(self.shipping_fee_type, "선택 안 함")

    @property
    def identity_key(self) -> str:
        return self.product_id or self.vendor_item_id or self.item_id or self.normalized_url

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def submission_mode_label(self) -> str:
        return SUBMISSION_MODE_LABELS.get(self.submission_mode, "선택 안 함")

    @property
    def translation_mode_label(self) -> str:
        return TRANSLATION_MODE_LABELS.get(self.translation_mode, self.translation_mode)

    @property
    def translation_status_label(self) -> str:
        return TRANSLATION_STATUS_LABELS.get(
            self.translation_status, self.translation_status
        )

    @property
    def can_retry(self) -> bool:
        if "자동 재시도 대상이 아닙니다" in (self.last_error or ""):
            return False
        return bool(
            self.search_value
            and self.shipping_fee_type in SHIPPING_FEE_LABELS
            and self.submission_mode in SUBMISSION_MODE_LABELS
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def touch(self) -> None:
        self.updated_at = _now()

    def refresh_readiness(self) -> None:
        if self.status in {
            STATUS_DRAFT_SAVED,
            STATUS_SUCCEEDED,
            STATUS_SKIPPED,
            STATUS_IN_PROGRESS,
            STATUS_AWAITING_CONFIRMATION,
        }:
            return
        if not self.search_value:
            self.status = STATUS_FAILED
            if not self.last_error:
                self.last_error = "유효한 쿠팡 상품 ID를 찾지 못했습니다."
        elif self.shipping_fee_type in SHIPPING_FEE_LABELS:
            if self.submission_mode in SUBMISSION_MODE_LABELS:
                translation_blocks_final = (
                    self.submission_mode == "final"
                    and self.translation_mode == "auto_ko"
                    and self.translation_status
                    in {"request_ready", "review_required", "failed"}
                )
                if translation_blocks_final:
                    self.status = STATUS_WAITING_TRANSLATION
                else:
                    self.status = STATUS_READY
                    self.last_error = ""
            else:
                self.status = STATUS_WAITING_SUBMISSION
                self.last_error = ""
        else:
            self.status = STATUS_WAITING_SHIPPING
            self.last_error = ""
        self.touch()


class BatchQueue:
    """한 번에 한 상품만 WING 진행 상태가 되도록 관리한다."""

    SCHEMA_VERSION = 6

    def __init__(self, jobs: Iterable[BatchJob] | None = None) -> None:
        self.jobs = list(jobs or ())

    def add_text(self, text: str) -> dict[str, int]:
        existing = {job.identity_key for job in self.jobs}
        added = duplicates = invalid = 0
        for url in extract_urls(text):
            job = BatchJob.from_url(url)
            if job.identity_key in existing:
                duplicates += 1
                continue
            self.jobs.append(job)
            existing.add(job.identity_key)
            added += 1
            if not job.search_value:
                invalid += 1
        return {"added": added, "duplicates": duplicates, "invalid": invalid}

    def get(self, job_id: str) -> BatchJob | None:
        return next((job for job in self.jobs if job.job_id == job_id), None)

    def set_shipping(self, job_id: str, shipping_fee_type: str | None) -> BatchJob:
        if shipping_fee_type is not None and shipping_fee_type not in SHIPPING_FEE_LABELS:
            raise ValueError("지원하지 않는 배송비 방식입니다.")
        job = self._require(job_id)
        job.shipping_fee_type = shipping_fee_type
        job.refresh_readiness()
        return job

    def set_selected(self, job_id: str, selected: bool) -> BatchJob:
        """작업 목록은 유지하면서 자동등록 대상 포함 여부를 바꾼다."""

        job = self._require(job_id)
        job.is_selected = bool(selected)
        job.touch()
        return job

    def set_submission_mode(
        self, job_id: str, submission_mode: str | None
    ) -> BatchJob:
        if submission_mode is not None and submission_mode not in SUBMISSION_MODE_LABELS:
            raise ValueError("지원하지 않는 등록 방식입니다.")
        job = self._require(job_id)
        job.submission_mode = submission_mode
        job.refresh_readiness()
        return job

    def set_translation_mode(self, job_id: str, translation_mode: str) -> BatchJob:
        if translation_mode not in TRANSLATION_MODE_LABELS:
            raise ValueError("지원하지 않는 상세이미지 번역 방식입니다.")
        job = self._require(job_id)
        job.translation_mode = translation_mode
        if translation_mode == "original":
            job.translation_status = "not_required"
            job.translation_review_approved = False
        elif job.translated_detail_image_paths:
            job.translation_status = (
                "approved" if job.translation_review_approved else "review_required"
            )
        elif job.source_detail_image_paths:
            job.translation_status = "request_ready"
        else:
            job.translation_status = "not_checked"
        job.refresh_readiness()
        return job

    def set_translation_source_images(
        self, job_id: str, paths: list[str]
    ) -> BatchJob:
        job = self._require(job_id)
        job.source_detail_image_paths = list(paths)
        job.translated_detail_image_paths = []
        job.translation_review_approved = False
        job.translation_status = "request_ready" if paths else "not_checked"
        job.refresh_readiness()
        return job

    def set_translated_images(self, job_id: str, paths: list[str]) -> BatchJob:
        job = self._require(job_id)
        job.translated_detail_image_paths = list(paths)
        job.translation_review_approved = False
        job.translation_status = "review_required" if paths else "request_ready"
        job.refresh_readiness()
        return job

    def set_option_images(self, job_id: str, paths: list[str]) -> BatchJob:
        job = self._require(job_id)
        job.source_option_image_paths = list(paths)
        job.watermarked_option_image_paths = []
        job.touch()
        return job

    def set_watermarked_option_images(
        self, job_id: str, paths: list[str]
    ) -> BatchJob:
        job = self._require(job_id)
        job.watermarked_option_image_paths = list(paths)
        job.touch()
        return job

    def approve_translation(self, job_id: str) -> BatchJob:
        job = self._require(job_id)
        if not job.translated_detail_image_paths:
            raise ValueError("검수할 번역 이미지가 없습니다.")
        job.translation_review_approved = True
        job.translation_status = "approved"
        job.refresh_readiness()
        return job

    def start_next(self) -> BatchJob | None:
        job = self.peek_next()
        if not job:
            return None
        if job.status in {STATUS_IN_PROGRESS, STATUS_AWAITING_CONFIRMATION}:
            return job
        job.status = STATUS_IN_PROGRESS
        job.attempts += 1
        job.last_error = ""
        job.automation_stage = "starting"
        job.automation_message = "WING 자동입력을 시작합니다."
        job.matched_options = []
        job.touch()
        return job

    def peek_next(self) -> BatchJob | None:
        """상태를 바꾸지 않고 현재 작업 또는 다음 등록 대기 작업을 반환한다."""

        current = next(
            (
                job
                for job in self.jobs
                if job.status in {STATUS_IN_PROGRESS, STATUS_AWAITING_CONFIRMATION}
            ),
            None,
        )
        if current:
            return current
        return next(
            (
                job
                for job in self.jobs
                if job.status == STATUS_READY and job.is_selected
            ),
            None,
        )

    def recover_interrupted_jobs(self) -> int:
        """이전 프로그램 실행에서 남은 진행 상태를 안전한 대기 상태로 되돌린다."""

        recovered = 0
        for job in self.jobs:
            if job.status != STATUS_IN_PROGRESS:
                continue
            job.status = STATUS_READY
            job.refresh_readiness()
            job.last_error = "이전 프로그램 종료로 등록 대기 상태로 자동 복구됨"
            job.touch()
            recovered += 1
        return recovered

    def mark_succeeded(
        self,
        job_id: str,
        *,
        registered_product_id: str = "",
        minimum_selling_price: int | None = None,
        country_of_origin: str = "",
    ) -> BatchJob:
        job = self._require(job_id)
        if job.submission_mode not in SUBMISSION_MODE_LABELS:
            raise ValueError("임시저장 또는 상품등록 방식을 먼저 선택하세요.")
        job.status = (
            STATUS_DRAFT_SAVED
            if job.submission_mode == "draft"
            else STATUS_SUCCEEDED
        )
        job.registered_product_id = registered_product_id.strip()
        job.last_error = ""
        job.automation_stage = "completed"
        job.automation_message = f"{job.submission_mode_label} 완료"
        self._apply_observation(job, minimum_selling_price, country_of_origin)
        job.touch()
        return job

    def mark_awaiting_confirmation(
        self,
        job_id: str,
        *,
        minimum_selling_price: int | None = None,
        country_of_origin: str = "",
        matched_options: list[dict] | None = None,
        message: str = "WING 입력 완료. 최종 실행 확인이 필요합니다.",
    ) -> BatchJob:
        """자동입력을 끝내고 외부 저장/등록 직전 상태로 둔다."""

        job = self._require(job_id)
        job.status = STATUS_AWAITING_CONFIRMATION
        job.automation_stage = "awaiting_confirmation"
        job.automation_message = message.strip()
        job.last_error = ""
        if matched_options is not None:
            job.matched_options = list(matched_options)
        self._apply_observation(job, minimum_selling_price, country_of_origin)
        job.touch()
        return job

    def mark_failed(self, job_id: str, error: str) -> BatchJob:
        job = self._require(job_id)
        job.status = STATUS_FAILED
        job.last_error = (error or "WING 등록 실패").strip()
        job.automation_stage = "failed"
        job.automation_message = job.last_error
        job.touch()
        return job

    def requeue_completed(self, job_id: str) -> BatchJob:
        """완료된 상품의 결과값을 비우고 같은 설정으로 다시 등록 대기시킨다."""

        job = self._require(job_id)
        if job.status not in {STATUS_DRAFT_SAVED, STATUS_SUCCEEDED}:
            raise ValueError("임시저장 또는 등록 완료된 상품만 다시 등록할 수 있습니다.")
        job.status = STATUS_READY
        job.minimum_selling_price = None
        job.country_of_origin = ""
        job.return_shipping_fee = None
        job.registered_product_id = ""
        job.last_error = ""
        job.automation_stage = ""
        job.automation_message = ""
        job.matched_options = []
        job.refresh_readiness()
        return job

    def skip(self, job_id: str) -> BatchJob:
        job = self._require(job_id)
        job.status = STATUS_SKIPPED
        job.touch()
        return job

    def remove(self, job_id: str) -> BatchJob:
        """선택 상품을 작업 목록에서 완전히 제거한다."""

        job = self._require(job_id)
        self.jobs.remove(job)
        return job

    def retry_failed(self, *, selected_only: bool = False) -> int:
        """재시도 가능한 실패 상품을 등록 대기로 되돌린다.

        ``selected_only``는 자동입력 시작 버튼에서 사용한다. 사용자가 맨 앞
        체크박스로 고른 실패 상품만 즉시 다시 시작하고, 체크하지 않은 실패
        기록은 그대로 보존한다.
        """

        retried = 0
        for job in self.jobs:
            if job.status != STATUS_FAILED or not job.can_retry:
                continue
            if selected_only and not job.is_selected:
                continue
            job.status = STATUS_READY
            job.last_error = ""
            job.automation_stage = ""
            job.automation_message = ""
            job.touch()
            retried += 1
        return retried

    def summary(self) -> dict[str, int]:
        counts = {status: 0 for status in STATUS_LABELS}
        for job in self.jobs:
            counts[job.status] = counts.get(job.status, 0) + 1
        counts["total"] = len(self.jobs)
        counts["selected"] = sum(1 for job in self.jobs if job.is_selected)
        counts["active"] = sum(counts.get(status, 0) for status in ACTIVE_STATUSES)
        return counts

    def to_dict(self) -> dict:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "saved_at": _now(),
            "jobs": [job.to_dict() for job in self.jobs],
        }

    @classmethod
    def from_dict(cls, value: dict) -> "BatchQueue":
        jobs = [
            BatchJob.from_dict(item)
            for item in value.get("jobs", [])
            if item.get("status") != STATUS_SKIPPED
        ]
        for job in jobs:
            if (
                job.status == STATUS_IN_PROGRESS
                and job.submission_mode not in SUBMISSION_MODE_LABELS
            ):
                job.status = STATUS_WAITING_SUBMISSION
                job.touch()
            elif job.status in {
                STATUS_WAITING_SHIPPING,
                STATUS_WAITING_SUBMISSION,
                STATUS_WAITING_TRANSLATION,
                STATUS_READY,
            }:
                job.refresh_readiness()
        return cls(jobs)

    @staticmethod
    def _apply_observation(
        job: BatchJob,
        minimum_selling_price: int | None,
        country_of_origin: str,
    ) -> None:
        if minimum_selling_price and minimum_selling_price > 0:
            job.minimum_selling_price = minimum_selling_price
            job.return_shipping_fee = build_return_shipping_fee_policy(
                minimum_selling_price
            )["target"]
        if country_of_origin.strip():
            job.country_of_origin = country_of_origin.strip()

    def _require(self, job_id: str) -> BatchJob:
        job = self.get(job_id)
        if not job:
            raise KeyError(f"작업을 찾을 수 없습니다: {job_id}")
        return job
