from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from poc.batch_jobs import STATUS_READY, BatchQueue
from poc.batch_store import load_batch_queue, save_batch_queue
from poc.wing_worker import _update


class BatchStoreTests(unittest.TestCase):
    def test_round_trips_batch_queue_as_utf8_json(self) -> None:
        queue = BatchQueue()
        queue.add_text("https://www.coupang.com/vp/products/9334344464")
        queue.set_shipping(queue.jobs[0].job_id, "free")
        queue.set_submission_mode(queue.jobs[0].job_id, "draft")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "current.json"
            save_batch_queue(queue, path)
            restored = load_batch_queue(path)
            raw = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(raw["schema_version"], 6)
        self.assertEqual(len(restored.jobs), 1)
        self.assertEqual(restored.jobs[0].shipping_fee_type, "free")
        self.assertEqual(restored.jobs[0].submission_mode, "draft")
        self.assertEqual(restored.jobs[0].status, STATUS_READY)

    def test_missing_file_returns_empty_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue = load_batch_queue(Path(temp_dir) / "missing.json")

        self.assertEqual(queue.jobs, [])

    def test_return_fee_adjustment_progress_persists_learned_maximum(self) -> None:
        queue = BatchQueue()
        queue.add_text("https://www.coupang.com/vp/products/9226911400")
        job = queue.jobs[0]
        job.return_shipping_fee = 30_000

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "current.json"
            save_batch_queue(queue, path)
            _update(
                path,
                job.job_id,
                "return_fee_adjusted",
                "WING 허용 범위에 맞춰 반품배송비를 20,000원으로 자동 보정했습니다.",
            )
            restored = load_batch_queue(path)

        self.assertEqual(restored.jobs[0].return_shipping_fee, 20_000)


if __name__ == "__main__":
    unittest.main()
