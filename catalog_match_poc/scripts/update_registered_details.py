"""Reapply cleaned full-width editor details to already registered WING products."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from playwright.sync_api import sync_playwright

from poc.settings_store import load_watermark_settings
from poc.seller_page_filter import filter_known_seller_pages
from poc.wing_automation import (
    WingAutomationError,
    _apply_configured_watermark,
    _continue_past_wing_password_guide,
    _detail_upload_assets,
    _last_visible,
    _upload_detail_as_editor,
    _visible_button_with_text,
    launch_controlled_chrome,
)


ASSET_ROOT = BASE_DIR / "data" / "wing_assets"
FIXED_SETTINGS = BASE_DIR / "data" / "settings" / "fixed_pages.json"
WATERMARK_SETTINGS = BASE_DIR / "data" / "settings" / "watermark.json"

REGISTERED_PRODUCTS = {
    "16348632469": "d11014d6deadb449",
    "16348743903": "6e0fc33880300124",
    "16348757692": "cd85ca476eb1e37c",
    "16348765437": "71e5f5b46c0d34a1",
}


def build_assets(job_id: str) -> tuple[list[str], list[str]]:
    fixed = json.loads(FIXED_SETTINGS.read_text(encoding="utf-8"))
    watermark = load_watermark_settings(WATERMARK_SETTINGS)
    work_root = ASSET_ROOT / job_id
    source = [
        str(path)
        for path in sorted((work_root / "source").glob("detail_*"))
        if path.is_file()
    ]
    kept, removed = filter_known_seller_pages(source)
    marked = _apply_configured_watermark(
        kept,
        output_dir=work_root / "update_watermarked",
        watermark_settings=watermark,
    )
    combined = list(fixed.get("top_assets") or []) + marked + list(
        fixed.get("bottom_assets") or []
    )
    assets = _detail_upload_assets(
        combined,
        output_dir=work_root / "editor_update",
    )
    return assets, removed


def request_review(page) -> str:
    old_url = page.url
    submit = _visible_button_with_text(
        page, re.compile(r"^\s*수정 및 검수 요청\s*$")
    )
    if submit.is_disabled():
        raise WingAutomationError("수정 및 검수 요청 버튼이 비활성화되어 있습니다.")
    submit.click()
    page.wait_for_timeout(800)

    alerts = page.locator(".sweet-alert:visible")
    if alerts.count():
        alert_text = " ".join(alerts.last.inner_text().split())
        if any(
            token in alert_text
            for token in ("입력항목", "확인해주세요", "필수항목", "입력해주세요")
        ):
            raise WingAutomationError(f"수정 검증 실패: {alert_text[:700]}")

    containers = page.locator(".modal:visible, .sweet-alert:visible")
    if containers.count():
        container = containers.last
        for name in ("수정 및 검수 요청", "수정 요청", "저장", "확인"):
            buttons = container.locator("button").filter(
                has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")
            )
            visible = [
                buttons.nth(index)
                for index in range(buttons.count())
                if buttons.nth(index).is_visible()
            ]
            if visible and not visible[-1].is_disabled():
                visible[-1].click()
                break

    deadline = time.monotonic() + 35
    body = ""
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        body = page.locator("body").inner_text()
        if page.url != old_url or any(
            token in body
            for token in (
                "수정요청이 완료되었습니다.",
                "수정 요청이 완료",
                "상품 수정이 완료",
                "수정이 완료",
                "검수 요청이 완료",
                "변경사항이 저장",
            )
        ):
            return page.url

    messages = [
        line.strip()
        for line in body.splitlines()
        if line.strip()
        and any(token in line for token in ("필수", "입력", "확인해주세요", "오류"))
    ]
    raise WingAutomationError(
        "수정 요청 완료를 확인하지 못했습니다: "
        + (" / ".join(messages[-5:])[:900] or "WING 화면을 확인하세요.")
    )


def update_one(page, registered_id: str, job_id: str) -> dict:
    assets, removed = build_assets(job_id)
    page.goto(
        "https://wing.coupang.com/tenants/seller-web/vendor-inventory/modify"
        f"?vendorInventoryId={registered_id}",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    _continue_past_wing_password_guide(page, page.url)
    page.locator('label[for="tab-content-level-0"]').wait_for(
        state="visible", timeout=60_000
    )
    page.wait_for_timeout(2_000)
    uploaded = _upload_detail_as_editor(page, assets)
    final_url = request_review(page)
    return {
        "registered_product_id": registered_id,
        "job_id": job_id,
        "uploaded": uploaded,
        "removed": [Path(path).name for path in removed],
        "final_url": final_url,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "registered_ids",
        nargs="*",
        default=list(REGISTERED_PRODUCTS),
    )
    parser.add_argument("--port", type=int, default=9223)
    args = parser.parse_args()

    unknown = [value for value in args.registered_ids if value not in REGISTERED_PRODUCTS]
    if unknown:
        raise SystemExit("Unknown registered product IDs: " + ", ".join(unknown))

    launch_controlled_chrome(
        profile_dir=BASE_DIR / "data" / "wing_chrome_profile",
        port=args.port,
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{args.port}"
        )
        if not browser.contexts:
            raise WingAutomationError("WING Chrome 컨텍스트를 찾지 못했습니다.")
        page = browser.contexts[0].new_page()
        try:
            for registered_id in args.registered_ids:
                try:
                    result = update_one(
                        page,
                        registered_id,
                        REGISTERED_PRODUCTS[registered_id],
                    )
                except Exception:
                    diagnostic_dir = BASE_DIR / "data" / "diagnostics"
                    diagnostic_dir.mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(diagnostic_dir / "registered_update_error.png"),
                        full_page=False,
                    )
                    (diagnostic_dir / "registered_update_error.html").write_text(
                        page.content(), encoding="utf-8"
                    )
                    raise
                print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            page.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
