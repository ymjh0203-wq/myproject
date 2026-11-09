from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from poc.settings_store import (
    load_fixed_page_settings,
    load_watermark_settings,
    save_fixed_page_settings,
    save_watermark_settings,
)


class SettingsStoreTests(unittest.TestCase):
    def test_uses_bundled_defaults_when_settings_do_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            top = root / "top.png"
            bottom = root / "bottom.png"
            top.touch()
            bottom.touch()

            settings = load_fixed_page_settings(
                root / "missing.json",
                default_top=top,
                default_bottom=bottom,
            )

        self.assertEqual(settings["top_assets"], [str(top)])
        self.assertEqual(settings["bottom_assets"], [str(bottom)])

    def test_saves_and_restores_changed_images_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings_path = root / "settings.json"
            top_default = root / "default_top.png"
            bottom_default = root / "default_bottom.png"
            top_default.touch()
            bottom_default.touch()

            save_fixed_page_settings(
                settings_path,
                top_assets=["C:/images/top1.png", "C:/images/top2.png"],
                bottom_assets=["C:/images/bottom.png"],
            )
            restored = load_fixed_page_settings(
                settings_path,
                default_top=top_default,
                default_bottom=bottom_default,
            )
            raw = json.loads(settings_path.read_text(encoding="utf-8"))

        self.assertEqual(raw["schema_version"], 1)
        self.assertEqual(
            restored["top_assets"],
            ["C:/images/top1.png", "C:/images/top2.png"],
        )
        self.assertEqual(restored["bottom_assets"], ["C:/images/bottom.png"])

    def test_saves_and_restores_watermark_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "watermark.json"
            save_watermark_settings(
                settings_path,
                enabled=True,
                image_path="C:/images/logo.png",
                text="리빙레드",
                position="top_left",
                opacity_percent=35,
                size_percent=18,
                angle_degrees=30,
            )

            restored = load_watermark_settings(settings_path)

        self.assertTrue(restored["enabled"])
        self.assertEqual(restored["image_path"], "C:/images/logo.png")
        self.assertEqual(restored["text"], "리빙레드")
        self.assertEqual(restored["position"], "top_left")
        self.assertEqual(restored["opacity_percent"], 35)
        self.assertEqual(restored["size_percent"], 18)
        self.assertEqual(restored["angle_degrees"], 30)


if __name__ == "__main__":
    unittest.main()
