"""기존 WING 임시저장 상품의 상세 이미지를 독립 섹션으로 다시 저장한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time

from playwright.sync_api import sync_playwright

from poc.settings_store import load_fixed_page_settings, load_watermark_settings
from poc.seller_page_filter import filter_known_seller_pages
from poc.wing_automation import (
    DEFAULT_DEBUG_PORT,
    WingAutomationError,
    _apply_configured_watermark,
    _connect_controlled_chrome,
    _detail_upload_assets,
    _download_source_details,
    _post_save_choice_visible,
    _upload_detail_as_editor,
    _visible_button_with_text,
    launch_controlled_chrome,
)


BASE_DIR = Path(__file__).resolve().parents[1]
PROFILE_DIR = BASE_DIR / "data" / "wing_chrome_profile"
FIXED_SETTINGS = BASE_DIR / "data" / "settings" / "fixed_pages.json"
WATERMARK_SETTINGS = BASE_DIR / "data" / "settings" / "watermark.json"
DEFAULT_TOP = BASE_DIR / "assets" / "fixed_pages" / "livingred_top.png"
DEFAULT_BOTTOM = BASE_DIR / "assets" / "fixed_pages" / "livingred_bottom.png"


def _prepare_assets(context, source_url: str, vendor_inventory_id: str) -> list[str]:
    work_root = BASE_DIR / "data" / "repairs" / vendor_inventory_id
    downloaded, _country = _download_source_details(
        context,
        {"source_url": source_url},
        work_root / "source",
    )
    detail_paths, _removed = filter_known_seller_pages(downloaded)
    if not detail_paths:
        raise WingAutomationError("원상품 상세 이미지를 다시 가져오지 못했습니다.")
    watermark = load_watermark_settings(WATERMARK_SETTINGS)
    detail_paths = _apply_configured_watermark(
        detail_paths,
        output_dir=work_root / "watermarked_detail",
        watermark_settings=watermark,
    )
    fixed = load_fixed_page_settings(
        FIXED_SETTINGS,
        default_top=DEFAULT_TOP,
        default_bottom=DEFAULT_BOTTOM,
    )
    assets = [
        *fixed["top_assets"],
        *detail_paths,
        *fixed["bottom_assets"],
    ]
    upload_assets = _detail_upload_assets(
        assets,
        output_dir=work_root / "separate_sections",
    )
    manifest = work_root / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "vendor_inventory_id": vendor_inventory_id,
                "source_url": source_url,
                "source_detail_count": len(detail_paths),
                "section_count": len(upload_assets),
                "assets": upload_assets,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return upload_assets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor-inventory-id", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    launch_controlled_chrome(profile_dir=PROFILE_DIR, port=DEFAULT_DEBUG_PORT)
    with sync_playwright() as playwright:
        browser = _connect_controlled_chrome(
            playwright,
            profile_dir=PROFILE_DIR,
            port=DEFAULT_DEBUG_PORT,
        )
        if not browser.contexts:
            raise WingAutomationError("자동입력 Chrome을 연결하지 못했습니다.")
        context = browser.contexts[0]
        assets = _prepare_assets(
            context,
            args.source_url,
            args.vendor_inventory_id,
        )
        print(json.dumps({"prepared_sections": len(assets)}, ensure_ascii=False))
        if not args.apply:
            return 0

        pages = [page for page in context.pages if "wing.coupang.com" in page.url]
        page = pages[-1] if pages else context.new_page()
        modify_url = (
            "https://wing.coupang.com/tenants/seller-web/vendor-inventory/modify"
            f"?vendorInventoryId={args.vendor_inventory_id}"
        )
        page.goto(modify_url, wait_until="domcontentloaded", timeout=60_000)
        page.get_by_text("현재 상품이 임시저장 되었습니다.", exact=False).wait_for(
            state="visible", timeout=60_000
        )
        uploaded = _upload_detail_as_editor(page, assets)
        if uploaded != len(assets):
            raise WingAutomationError(
                f"상세 이미지 분리 입력 수가 맞지 않습니다. ({uploaded}/{len(assets)})"
            )
        save = _visible_button_with_text(page, re.compile(r"^\s*임시\s*저장\s*$"))
        save.click()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if _post_save_choice_visible(page):
                print(
                    json.dumps(
                        {"saved": True, "separate_sections": uploaded},
                        ensure_ascii=False,
                    )
                )
                return 0
            alerts = page.locator(".sweet-alert:visible")
            if alerts.count():
                text = " ".join(alerts.last.inner_text().split())
                confirm = alerts.last.locator("button.confirm, button.alert-confirm")
                if confirm.count() and any(
                    token in text for token in ("저장", "확인", "반품배송비")
                ):
                    confirm.last.click(force=True)
                elif text:
                    raise WingAutomationError(f"WING 임시저장 확인 필요: {text[:500]}")
            page.wait_for_timeout(500)
        raise WingAutomationError("분리된 상세 이미지의 임시저장 완료를 확인하지 못했습니다.")


if __name__ == "__main__":
    raise SystemExit(main())
