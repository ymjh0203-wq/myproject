from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from poc import wing_worker


class WingWorkerTests(unittest.TestCase):
    def test_prepare_mode_continues_directly_to_final_submission(self) -> None:
        argv = [
            "wing_worker",
            "--mode",
            "prepare",
            "--job-id",
            "job-1",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(wing_worker, "_prepare", return_value=0) as prepare,
            patch.object(wing_worker, "_submit", return_value=0) as submit,
        ):
            result = wing_worker.main()

        self.assertEqual(result, 0)
        prepare.assert_called_once()
        submit.assert_called_once()
        self.assertIs(prepare.call_args.args[0], submit.call_args.args[0])

    def test_prepare_failure_does_not_attempt_submission(self) -> None:
        argv = [
            "wing_worker",
            "--mode",
            "prepare",
            "--job-id",
            "job-1",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(wing_worker, "_prepare", return_value=1),
            patch.object(wing_worker, "_submit") as submit,
        ):
            result = wing_worker.main()

        self.assertEqual(result, 1)
        submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
