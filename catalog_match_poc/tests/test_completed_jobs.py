from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from poc.batch_jobs import STATUS_FAILED, BatchQueue
from poc.completed_jobs import archive_and_remove_completed


URL_1 = "https://www.coupang.com/vp/products/9334344464?itemId=27677709636"
URL_2 = "https://www.coupang.com/vp/products/9164263934?itemId=111"


class CompletedJobsTests(unittest.TestCase):
    def test_archives_and_removes_only_completed_jobs(self) -> None:
        queue = BatchQueue()
        queue.add_text(f"{URL_1}\n{URL_2}")
        completed, failed = queue.jobs
        queue.set_shipping(completed.job_id, "free")
        queue.set_submission_mode(completed.job_id, "final")
        queue.mark_succeeded(
            completed.job_id,
            registered_product_id="123456789",
            minimum_selling_price=36_160,
        )
        queue.mark_failed(failed.job_id, "진단 필요")

        with TemporaryDirectory() as directory:
            paths = archive_and_remove_completed(queue, Path(directory))

            self.assertEqual(len(paths), 1)
            self.assertIsNone(queue.get(completed.job_id))
            self.assertEqual(queue.get(failed.job_id).status, STATUS_FAILED)
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["report_type"], "batch_completion")
            self.assertEqual(
                payload["product"]["registered_product_id"],
                "123456789",
            )


if __name__ == "__main__":
    unittest.main()
