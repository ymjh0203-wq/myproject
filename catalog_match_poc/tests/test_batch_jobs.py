from __future__ import annotations

import unittest

from poc.batch_jobs import (
    STATUS_DRAFT_SAVED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_READY,
    STATUS_SUCCEEDED,
    STATUS_SKIPPED,
    STATUS_WAITING_SHIPPING,
    STATUS_WAITING_SUBMISSION,
    STATUS_WAITING_TRANSLATION,
    BatchQueue,
    extract_urls,
)


URL_1 = (
    "https://www.coupang.com/vp/products/9334344464?"
    "itemId=27677709636&vendorItemId=95491643891"
)
URL_2 = "https://www.coupang.com/vp/products/9164263934"


class BatchQueueTests(unittest.TestCase):
    def test_extracts_plain_and_markdown_urls_without_duplicates(self) -> None:
        urls = extract_urls(f"{URL_1}\n[{URL_2}]({URL_2})")

        self.assertEqual(urls, [URL_1, URL_2])

    def test_adds_multiple_urls_and_rejects_duplicates(self) -> None:
        queue = BatchQueue()

        result = queue.add_text(f"{URL_1}\n{URL_2}\n{URL_1}")

        self.assertEqual(result, {"added": 2, "duplicates": 0, "invalid": 0})
        self.assertEqual(len(queue.jobs), 2)
        self.assertTrue(
            all(job.status == STATUS_WAITING_SHIPPING for job in queue.jobs)
        )

        second_result = queue.add_text(URL_1)
        self.assertEqual(second_result["duplicates"], 1)

    def test_shipping_and_submission_choices_make_job_ready(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]

        queue.set_shipping(job.job_id, "free")

        self.assertEqual(job.status, STATUS_WAITING_SUBMISSION)
        self.assertEqual(job.shipping_fee_label, "무료배송")

        queue.set_submission_mode(job.job_id, "draft")

        self.assertEqual(job.status, STATUS_READY)
        self.assertEqual(job.submission_mode_label, "임시저장")

    def test_same_product_with_different_tracking_query_is_duplicate(self) -> None:
        queue = BatchQueue()
        first = f"{URL_2}?searchId=first"
        second = f"{URL_2}?searchId=second"

        result = queue.add_text(f"{first}\n{second}")

        self.assertEqual(result["added"], 1)
        self.assertEqual(result["duplicates"], 1)

    def test_only_one_job_is_in_progress_at_a_time(self) -> None:
        queue = BatchQueue()
        queue.add_text(f"{URL_1}\n{URL_2}")
        for job in queue.jobs:
            queue.set_shipping(job.job_id, "cash_on_delivery")
            queue.set_submission_mode(job.job_id, "final")

        first = queue.start_next()
        same_first = queue.start_next()

        self.assertIsNotNone(first)
        self.assertEqual(first.job_id, same_first.job_id)
        self.assertEqual(first.status, STATUS_IN_PROGRESS)
        self.assertEqual(first.attempts, 1)
        self.assertEqual(queue.jobs[1].status, STATUS_READY)

    def test_peek_next_does_not_mark_job_in_progress(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]
        queue.set_shipping(job.job_id, "free")
        queue.set_submission_mode(job.job_id, "final")

        selected = queue.peek_next()

        self.assertEqual(selected.job_id, job.job_id)
        self.assertEqual(job.status, STATUS_READY)
        self.assertEqual(job.attempts, 0)

    def test_unselected_ready_job_is_skipped_without_being_removed(self) -> None:
        queue = BatchQueue()
        queue.add_text(f"{URL_1}\n{URL_2}")
        for job in queue.jobs:
            queue.set_shipping(job.job_id, "free")
            queue.set_submission_mode(job.job_id, "final")
        first, second = queue.jobs

        queue.set_selected(first.job_id, False)
        selected = queue.start_next()

        self.assertEqual(selected.job_id, second.job_id)
        self.assertEqual(first.status, STATUS_READY)
        self.assertFalse(first.is_selected)
        self.assertEqual(len(queue.jobs), 2)

    def test_selection_is_saved_and_old_jobs_default_to_selected(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]
        queue.set_selected(job.job_id, False)

        restored = BatchQueue.from_dict(queue.to_dict())
        legacy = job.to_dict()
        legacy.pop("is_selected")
        restored_legacy = BatchQueue.from_dict({"jobs": [legacy]})

        self.assertFalse(restored.jobs[0].is_selected)
        self.assertTrue(restored_legacy.jobs[0].is_selected)

    def test_recovers_interrupted_job_to_safe_waiting_state(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]
        queue.set_shipping(job.job_id, "free")
        queue.set_submission_mode(job.job_id, "final")
        queue.start_next()

        recovered = queue.recover_interrupted_jobs()

        self.assertEqual(recovered, 1)
        self.assertEqual(job.status, STATUS_READY)
        self.assertIn("자동 복구", job.last_error)
        self.assertEqual(job.attempts, 1)

    def test_success_records_calculated_values_then_moves_to_next(self) -> None:
        queue = BatchQueue()
        queue.add_text(f"{URL_1}\n{URL_2}")
        for job in queue.jobs:
            queue.set_shipping(job.job_id, "free")
            queue.set_submission_mode(job.job_id, "final")
        first = queue.start_next()

        queue.mark_succeeded(
            first.job_id,
            registered_product_id="16342255417",
            minimum_selling_price=86_400,
            country_of_origin="중국",
        )
        second = queue.start_next()

        self.assertEqual(first.status, STATUS_SUCCEEDED)
        self.assertEqual(first.return_shipping_fee, 80_000)
        self.assertEqual(first.country_of_origin, "중국")
        self.assertEqual(first.registered_product_id, "16342255417")
        self.assertNotEqual(first.job_id, second.job_id)

    def test_draft_choice_records_draft_saved_result(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]
        queue.set_shipping(job.job_id, "free")
        queue.set_submission_mode(job.job_id, "draft")
        queue.start_next()

        queue.mark_succeeded(job.job_id)

        self.assertEqual(job.status, STATUS_DRAFT_SAVED)
        self.assertEqual(job.registered_product_id, "")

    def test_completed_job_can_be_requeued_with_choices_preserved(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]
        queue.set_shipping(job.job_id, "free")
        queue.set_submission_mode(job.job_id, "final")
        queue.start_next()
        queue.mark_succeeded(
            job.job_id,
            registered_product_id="12345",
            minimum_selling_price=86_400,
            country_of_origin="중국",
        )

        queue.requeue_completed(job.job_id)

        self.assertEqual(job.status, STATUS_READY)
        self.assertEqual(job.shipping_fee_type, "free")
        self.assertEqual(job.submission_mode, "final")
        self.assertEqual(job.registered_product_id, "")
        self.assertIsNone(job.minimum_selling_price)
        self.assertIsNone(job.return_shipping_fee)
        self.assertEqual(job.country_of_origin, "")
        self.assertEqual(job.attempts, 1)

    def test_active_job_cannot_be_requeued_as_completed(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]
        queue.set_shipping(job.job_id, "free")
        queue.set_submission_mode(job.job_id, "final")

        with self.assertRaises(ValueError):
            queue.requeue_completed(job.job_id)

    def test_retry_moves_valid_failed_job_back_to_ready(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]
        queue.set_shipping(job.job_id, "free")
        queue.set_submission_mode(job.job_id, "final")
        queue.start_next()
        queue.mark_failed(job.job_id, "WING 입력 오류")

        count = queue.retry_failed()

        self.assertEqual(count, 1)
        self.assertEqual(job.status, STATUS_READY)
        self.assertEqual(job.last_error, "")

    def test_product_without_sellable_winner_is_not_retried_forever(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]
        queue.set_shipping(job.job_id, "free")
        queue.set_submission_mode(job.job_id, "final")
        queue.mark_failed(
            job.job_id,
            "판매 가능한 옵션이 없어 자동 재시도 대상이 아닙니다.",
        )

        self.assertFalse(job.can_retry)
        self.assertEqual(queue.retry_failed(), 0)
        self.assertEqual(job.status, STATUS_FAILED)

    def test_start_retry_only_requeues_selected_failed_jobs(self) -> None:
        queue = BatchQueue()
        queue.add_text(f"{URL_1}\n{URL_2}")
        selected, excluded = queue.jobs
        for job in queue.jobs:
            queue.set_shipping(job.job_id, "free")
            queue.set_submission_mode(job.job_id, "draft")
            job.status = STATUS_FAILED
            job.last_error = "WING 입력 오류"
        queue.set_selected(excluded.job_id, False)

        count = queue.retry_failed(selected_only=True)

        self.assertEqual(count, 1)
        self.assertEqual(selected.status, STATUS_READY)
        self.assertEqual(selected.last_error, "")
        self.assertEqual(excluded.status, STATUS_FAILED)
        self.assertEqual(excluded.last_error, "WING 입력 오류")

    def test_invalid_url_stays_failed_and_is_not_retried(self) -> None:
        queue = BatchQueue()

        result = queue.add_text("https://example.com/not-coupang")
        queue.set_shipping(queue.jobs[0].job_id, "free")

        self.assertEqual(result["invalid"], 1)
        self.assertEqual(queue.jobs[0].status, STATUS_FAILED)
        self.assertEqual(queue.retry_failed(), 0)

    def test_old_ready_job_requires_safe_submission_choice_after_loading(self) -> None:
        original = BatchQueue()
        original.add_text(URL_1)
        job_data = original.jobs[0].to_dict()
        job_data["shipping_fee_type"] = "free"
        job_data["status"] = STATUS_READY
        job_data.pop("submission_mode")

        restored = BatchQueue.from_dict({"jobs": [job_data]})

        self.assertEqual(restored.jobs[0].status, STATUS_WAITING_SUBMISSION)
        self.assertIsNone(restored.jobs[0].submission_mode)

    def test_final_registration_waits_for_translation_review(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]
        queue.set_shipping(job.job_id, "free")
        queue.set_submission_mode(job.job_id, "final")

        queue.set_translation_source_images(job.job_id, ["source.jpg"])

        self.assertEqual(job.status, STATUS_WAITING_TRANSLATION)
        self.assertIsNone(queue.start_next())

    def test_approved_translation_allows_final_registration(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]
        queue.set_shipping(job.job_id, "free")
        queue.set_submission_mode(job.job_id, "final")
        queue.set_translation_source_images(job.job_id, ["source.jpg"])
        queue.set_translated_images(job.job_id, ["translated.jpg"])

        queue.approve_translation(job.job_id)

        self.assertEqual(job.status, STATUS_READY)
        self.assertEqual(job.translation_status, "approved")
        self.assertTrue(job.translation_review_approved)

    def test_draft_can_continue_while_translation_needs_review(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]
        queue.set_shipping(job.job_id, "free")
        queue.set_submission_mode(job.job_id, "draft")
        queue.set_translation_source_images(job.job_id, ["source.jpg"])

        self.assertEqual(job.status, STATUS_READY)

    def test_records_source_and_watermarked_option_images(self) -> None:
        queue = BatchQueue()
        queue.add_text(URL_1)
        job = queue.jobs[0]

        queue.set_option_images(job.job_id, ["option-red.jpg", "option-blue.jpg"])
        queue.set_watermarked_option_images(
            job.job_id, ["marked-red.jpg", "marked-blue.jpg"]
        )

        self.assertEqual(job.source_option_image_paths, ["option-red.jpg", "option-blue.jpg"])
        self.assertEqual(
            job.watermarked_option_image_paths,
            ["marked-red.jpg", "marked-blue.jpg"],
        )

    def test_removes_selected_job_from_queue(self) -> None:
        queue = BatchQueue()
        queue.add_text(f"{URL_1}\n{URL_2}")
        removed_id = queue.jobs[0].job_id

        removed = queue.remove(removed_id)

        self.assertEqual(removed.job_id, removed_id)
        self.assertIsNone(queue.get(removed_id))
        self.assertEqual(len(queue.jobs), 1)

    def test_drops_legacy_skipped_jobs_when_loading(self) -> None:
        queue = BatchQueue()
        queue.add_text(f"{URL_1}\n{URL_2}")
        payload = queue.to_dict()
        payload["jobs"][0]["status"] = STATUS_SKIPPED

        restored = BatchQueue.from_dict(payload)

        self.assertEqual(len(restored.jobs), 1)
        self.assertNotEqual(restored.jobs[0].status, STATUS_SKIPPED)


if __name__ == "__main__":
    unittest.main()
