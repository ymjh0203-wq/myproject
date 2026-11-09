"""Capture the current controlled-WING page for automation diagnostics."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "data" / "diagnostics"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9223")
        context = browser.contexts[0]
        page = next(
            (candidate for candidate in context.pages if "wing.coupang.com" in candidate.url),
            context.pages[-1],
        )
        screenshot = OUTPUT_DIR / "wing_current.png"
        html = OUTPUT_DIR / "wing_current.html"
        text_dump = OUTPUT_DIR / "wing_current.txt"
        try:
            page.screenshot(path=str(screenshot), full_page=False, timeout=5_000)
        except Exception as exc:
            print("screenshot skipped:", exc)
        html.write_text(page.content(), encoding="utf-8")
        text_dump.write_text(page.locator("body").inner_text(), encoding="utf-8")
        print(page.url)
        print(page.title())
        for radio_id in ("tab-content-level-0", "tab-content-0", "tab-content-1"):
            radio = page.locator(f"#{radio_id}")
            label = page.locator(f'label[for="{radio_id}"]')
            if radio.count() and label.count():
                print(
                    radio_id,
                    "checked=",
                    radio.is_checked(),
                    "uicfg=",
                    label.get_attribute("data-sc-common-uicfg"),
                )
        visible_inputs = page.locator('input[type="text"][placeholder=""]')
        print("visible blank inputs:")
        for index in range(visible_inputs.count()):
            field = visible_inputs.nth(index)
            if not field.is_visible() or field.is_disabled():
                continue
            print(
                index,
                "class=",
                field.get_attribute("class"),
                "outer=",
                " ".join(
                    field.locator("xpath=..").inner_text().split()
                )[:180],
            )
        print(" ".join(page.locator("body").inner_text().split())[:2_000])
        print(screenshot)
        print(html)
        print(text_dump)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
