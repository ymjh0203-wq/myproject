# ============================================================
# login_setup.py  —  타오바오 최초 로그인(세션 저장) 도구
# ------------------------------------------------------------
# *** 이 스크립트는 반드시 사용자가 직접 실행해야 합니다 ***
# (타오바오_로그인_설정.bat 더블클릭, 또는 터미널에서 직접 실행)
#
# 비밀번호는 이 스크립트가 절대 다루지 않습니다 — 뜨는 브라우저 창에
# 사용자가 직접 로그인하고, 그 "로그인된 상태"만 프로필 폴더에 저장해서
# 나중에 collect.py 가 재사용합니다.
#
# 저장 위치: 상품등록/.browser_profile/  (쿠키 포함 → 남과 공유 금지, .gitignore됨)
# ============================================================

import os

from playwright.sync_api import sync_playwright

# collect.py / taobao.py 와 "같은" 프로필 폴더를 써야 로그인 상태가 공유됩니다.
from collector.taobao import DEFAULT_PROFILE_DIR


def main() -> None:
    os.makedirs(DEFAULT_PROFILE_DIR, exist_ok=True)
    print("=" * 60)
    print("타오바오 로그인 세션 저장 도구")
    print("=" * 60)
    print("1. 잠시 후 브라우저 창이 새로 열립니다.")
    print("2. 그 창에서 직접 타오바오(taobao.com)에 로그인해주세요.")
    print("   (캡차/슬라이더가 뜨면 그 창에서 직접 풀어주세요)")
    print("3. 로그인이 끝나면 상품 상세페이지 한 개를 열어 가격/옵션이")
    print("   제대로 보이는지 확인해보셔도 좋습니다.")
    print("4. 다 되셨으면 이 터미널로 돌아와 Enter를 눌러주세요.")
    print()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            DEFAULT_PROFILE_DIR,
            headless=False,
            locale="zh-CN",
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://login.taobao.com")

        input("로그인을 마치셨으면 Enter를 눌러주세요... ")

        last_url = page.url
        context.close()

    print()
    print(f"로그인 상태가 저장되었습니다: {DEFAULT_PROFILE_DIR}")
    print(f"마지막으로 보던 주소: {last_url}")
    print()
    print("이제 수집을 실행할 수 있습니다:")
    print('  python collect.py "https://item.taobao.com/item.htm?id=상품ID"')


if __name__ == "__main__":
    main()
