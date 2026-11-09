from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from poc.report_store import redact_sensitive_text, save_report, strip_url_query


class ReportStoreTests(unittest.TestCase):
    def test_redacts_common_secret_formats(self) -> None:
        value = "password=hunter2 cookie:session123 access_token=abc123"
        redacted = redact_sensitive_text(value)
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("session123", redacted)
        self.assertNotIn("abc123", redacted)

    def test_wing_url_drops_query_and_fragment(self) -> None:
        value = strip_url_query("https://wing.coupang.com/path?token=secret#area")
        self.assertEqual(value, "https://wing.coupang.com/path")

    def test_saves_utf8_json_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = save_report(
                {"product": {"product_id": "123"}, "note": "한글", "secret": "cookie=bad"},
                Path(directory),
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["note"], "한글")
            self.assertNotIn("bad", payload["secret"])
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_two_fast_saves_do_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = save_report({"product": {"product_id": "123"}}, Path(directory))
            second = save_report({"product": {"product_id": "123"}}, Path(directory))
            self.assertNotEqual(first, second)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())


if __name__ == "__main__":
    unittest.main()
