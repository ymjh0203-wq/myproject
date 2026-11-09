"""Prepare per-image upload folders for existing WING draft products.

The original automation stored detailed product images separately, then combined
them into a few WING editor panels.  This helper recreates upload sets while
preserving every image boundary so each image can be edited or deleted on its
own in WING.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from poc.wing_automation import _detail_upload_assets


DATA_DIR = BASE_DIR / "data"
REPORT_DIR = DATA_DIR / "reports" / "completed_jobs"
ASSET_DIR = DATA_DIR / "wing_assets"
REPAIR_DIR = DATA_DIR / "repairs"
FIXED_SETTINGS = DATA_DIR / "settings" / "fixed_pages.json"
DEFAULT_TOP = BASE_DIR / "assets" / "fixed_pages" / "livingred_top.png"
DEFAULT_BOTTOM = BASE_DIR / "assets" / "fixed_pages" / "livingred_bottom.png"


def _natural_key(path: Path) -> tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    )


def _existing_fixed_pages() -> tuple[list[Path], list[Path]]:
    settings = json.loads(FIXED_SETTINGS.read_text(encoding="utf-8"))
    top = [Path(value) for value in settings.get("top_assets", [])]
    bottom = [Path(value) for value in settings.get("bottom_assets", [])]
    top = [path for path in top if path.is_file()] or [DEFAULT_TOP]
    bottom = [path for path in bottom if path.is_file()] or [DEFAULT_BOTTOM]
    return top, bottom


def _draft_reports() -> list[dict]:
    reports: list[dict] = []
    for report_path in sorted(REPORT_DIR.glob("phase0_*.json")):
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        completion = report.get("completion") or {}
        product = report.get("product") or {}
        vendor_inventory_id = str(
            product.get("registered_product_id")
            or completion.get("registered_product_id")
            or ""
        ).strip()
        if completion.get("status") != "draft_saved" or not vendor_inventory_id:
            continue
        reports.append(
            {
                "report_path": str(report_path),
                "product_id": str(product.get("product_id") or completion.get("product_id") or ""),
                "vendor_inventory_id": vendor_inventory_id,
                "job_id": str(completion.get("job_id") or ""),
                "source_url": str(completion.get("source_url") or ""),
                "updated_at": str(completion.get("updated_at") or ""),
            }
        )
    latest_by_vendor: dict[str, dict] = {}
    for report in reports:
        latest_by_vendor[report["vendor_inventory_id"]] = report
    return sorted(latest_by_vendor.values(), key=lambda item: item["updated_at"])


def prepare(report: dict, top: list[Path], bottom: list[Path]) -> dict:
    vendor_inventory_id = report["vendor_inventory_id"]
    source_dir = ASSET_DIR / report["job_id"] / "watermarked_detail"
    details = sorted(
        (path for path in source_dir.iterdir() if path.is_file()),
        key=_natural_key,
    )
    if not details:
        raise FileNotFoundError(f"No detail images: {source_dir}")

    work_root = REPAIR_DIR / vendor_inventory_id
    assets = [*(str(path) for path in top), *(str(path) for path in details), *(str(path) for path in bottom)]
    upload_assets = _detail_upload_assets(
        assets,
        output_dir=work_root / "separate_sections",
    )

    manual_dir = work_root / f"수동업로드_{len(upload_assets)}장"
    manual_dir.mkdir(parents=True, exist_ok=True)
    manual_paths: list[str] = []
    for index, source in enumerate(upload_assets, start=1):
        source_path = Path(source)
        destination = manual_dir / f"{index:02d}{source_path.suffix.lower()}"
        shutil.copy2(source_path, destination)
        manual_paths.append(str(destination))

    manifest = {
        **report,
        "source_detail_count": len(details),
        "section_count": len(upload_assets),
        "assets": upload_assets,
        "manual_upload_dir": str(manual_dir),
        "manual_upload_files": manual_paths,
    }
    work_root.mkdir(parents=True, exist_ok=True)
    (work_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()

    top, bottom = _existing_fixed_pages()
    results: list[dict] = []
    for report in _draft_reports():
        if report["vendor_inventory_id"] in args.exclude:
            continue
        results.append(prepare(report, top, bottom))

    print(
        json.dumps(
            {
                "prepared_count": len(results),
                "products": [
                    {
                        "product_id": item["product_id"],
                        "vendor_inventory_id": item["vendor_inventory_id"],
                        "section_count": item["section_count"],
                        "manual_upload_dir": item["manual_upload_dir"],
                    }
                    for item in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
