"""Inspect WING's detail-mode confirmation without submitting product changes."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "data" / "diagnostics"
PRODUCT_URL = (
    "https://wing.coupang.com/tenants/seller-web/vendor-inventory/modify"
    "?vendorInventoryId=16348632469"
)


def state(page, radio_id: str) -> tuple[bool, str]:
    radio = page.locator(f"#{radio_id}")
    label = page.locator(f'label[for="{radio_id}"]')
    return radio.is_checked(), label.get_attribute("data-sc-common-uicfg") or ""


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9223")
        context = browser.contexts[0]
        page = context.new_page()
        try:
            page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=60_000)
            image_label = page.locator('label[for="tab-content-0"]')
            image_label.wait_for(state="visible", timeout=60_000)
            page.wait_for_timeout(1_000)
            print("before", state(page, "tab-content-0"), state(page, "tab-content-1"))
            image_label.click()
            page.wait_for_timeout(1_000)
            print("after", state(page, "tab-content-0"), state(page, "tab-content-1"))
            visible_containers = page.locator(".modal:visible, .sweet-alert:visible")
            print("visible containers", visible_containers.count())
            for index in range(visible_containers.count()):
                container = visible_containers.nth(index)
                print("container", index, " ".join(container.inner_text().split()))
                buttons = container.locator("button:visible")
                print(
                    "buttons",
                    [" ".join(buttons.nth(i).inner_text().split()) for i in range(buttons.count())],
                )
            confirm = page.get_by_role("button", name="확인", exact=True)
            if confirm.count():
                confirm.last.click()
                page.wait_for_timeout(1_000)
                print(
                    "after confirm",
                    state(page, "tab-content-0"),
                    state(page, "tab-content-1"),
                )
                page.wait_for_timeout(4_000)
                print(
                    "four seconds later",
                    state(page, "tab-content-0"),
                    state(page, "tab-content-1"),
                )
            page.screenshot(path=str(OUTPUT_DIR / "wing_tab_switch.png"))
            (OUTPUT_DIR / "wing_tab_switch.html").write_text(
                page.content(), encoding="utf-8"
            )
        finally:
            page.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
