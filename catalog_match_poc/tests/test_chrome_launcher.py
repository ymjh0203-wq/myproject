from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from poc.chrome_launcher import ChromeLaunchError, find_chrome, launch_chrome


class ChromeLauncherTests(unittest.TestCase):
    def test_finds_chrome_in_local_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chrome = root / "Google" / "Chrome" / "Application" / "chrome.exe"
            chrome.parent.mkdir(parents=True)
            chrome.touch()
            self.assertEqual(find_chrome({"LOCALAPPDATA": str(root)}), chrome)

    def test_launches_only_the_selected_chrome_executable(self) -> None:
        calls = []

        def fake_popen(args, **kwargs):
            calls.append((args, kwargs))
            return object()

        chrome = Path("C:/Chrome/chrome.exe")
        opened = launch_chrome(
            "https://wing.coupang.com/", executable=chrome, popen=fake_popen
        )
        self.assertEqual(opened, chrome)
        self.assertEqual(calls[0][0], [str(chrome), "https://wing.coupang.com/"])
        self.assertTrue(calls[0][1]["close_fds"])

    def test_reports_chrome_start_failure(self) -> None:
        def failing_popen(_args, **_kwargs):
            raise OSError("blocked")

        with self.assertRaises(ChromeLaunchError):
            launch_chrome(
                "https://wing.coupang.com/",
                executable=Path("C:/Chrome/chrome.exe"),
                popen=failing_popen,
            )


if __name__ == "__main__":
    unittest.main()
