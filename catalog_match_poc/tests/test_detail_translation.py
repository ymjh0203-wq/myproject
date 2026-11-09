from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from poc.detail_translation import (
    apply_watermark_to_images,
    build_localization_prompt,
    delete_translation_job,
    estimate_localization_workload,
    import_translation_results,
    prepare_option_images,
    prepare_translation_job,
)


class DetailTranslationTests(unittest.TestCase):
    def test_prompt_detects_language_and_naturalizes_korean(self) -> None:
        prompt = build_localization_prompt(["detail_001.jpg"])

        self.assertIn("문구 언어와 문장 품질을 먼저 확인", prompt)
        self.assertIn("중국어(간체·번체)", prompt)
        self.assertIn("한국어가 기계 번역투이거나 어색하면", prompt)
        self.assertIn("이미 자연스러운 한국어 문구", prompt)
        self.assertIn("제품 사진", prompt)
        self.assertIn("숫자, 치수, 단위, 모델명", prompt)
        self.assertIn("사람이 미리보기 검수", prompt)
        self.assertIn("별도 후처리 단계", prompt)

    def test_estimates_daily_workload_with_retry_allowance(self) -> None:
        estimate = estimate_localization_workload(10, 5)

        self.assertEqual(estimate["base_images"], 50)
        self.assertEqual(estimate["retry_images"], 10)
        self.assertEqual(estimate["estimated_total_images"], 60)

    def test_prepares_non_destructive_translation_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original = root / "original.jpg"
            original.write_bytes(b"fake-image")

            manifest = prepare_translation_job(
                job_id="abcd1234",
                source_paths=[str(original)],
                output_root=root / "jobs",
            )
            copied = Path(manifest["source_images"][0])
            saved = json.loads(
                (root / "jobs" / "abcd1234" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(original.read_bytes(), b"fake-image")
            self.assertEqual(copied.read_bytes(), b"fake-image")
            self.assertEqual(saved["status"], "request_ready")
            self.assertEqual(saved["schema_version"], 2)
            self.assertTrue(saved["requirements"]["detect_language_per_image"])
            self.assertTrue(saved["requirements"]["rewrite_awkward_korean"])

    def test_imports_translated_results_in_selected_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.png"
            second = root / "second.jpg"
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            imported = import_translation_results(
                job_id="abcd1234",
                result_paths=[str(first), str(second)],
                output_root=root / "jobs",
            )

            self.assertEqual(Path(imported[0]).read_bytes(), b"first")
            self.assertEqual(Path(imported[1]).read_bytes(), b"second")

    def test_applies_watermark_to_copy_without_changing_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            mark = root / "mark.png"
            Image.new("RGB", (100, 100), (20, 80, 160)).save(source)
            Image.new("RGBA", (20, 10), (240, 30, 30, 255)).save(mark)
            original_bytes = source.read_bytes()

            results = apply_watermark_to_images(
                source_paths=[str(source)],
                output_dir=root / "watermarked",
                image_path=str(mark),
                position="bottom_right",
                opacity_percent=100,
                size_percent=20,
            )

            self.assertEqual(source.read_bytes(), original_bytes)
            output = Image.open(results[0]).convert("RGB")
            self.assertEqual(output.getpixel((5, 5)), (20, 80, 160))
            self.assertEqual(output.getpixel((75, 85)), (240, 30, 30))

    def test_prepares_option_images_in_selected_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "red.png"
            second = root / "blue.jpg"
            first.write_bytes(b"red")
            second.write_bytes(b"blue")

            copied = prepare_option_images(
                job_id="abcd1234",
                source_paths=[str(first), str(second)],
                output_root=root / "jobs",
            )

            self.assertEqual(Path(copied[0]).name, "option_001.png")
            self.assertEqual(Path(copied[1]).name, "option_002.jpg")
            self.assertEqual(Path(copied[0]).read_bytes(), b"red")

    def test_deletes_only_selected_translation_job_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "jobs"
            target = root / "abcd1234"
            other = root / "other5678"
            target.mkdir(parents=True)
            other.mkdir(parents=True)
            (target / "result.png").write_bytes(b"result")

            removed = delete_translation_job(
                job_id="abcd1234", output_root=root
            )

            self.assertTrue(removed)
            self.assertFalse(target.exists())
            self.assertTrue(other.exists())


if __name__ == "__main__":
    unittest.main()
