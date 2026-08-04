"""WING 로그인 세션 저장 도구.

*** 이 스크립트는 반드시 사용자가 직접 실행해야 합니다 ***
(윙_로그인_설정.bat 더블클릭, 또는 터미널에서 직접 실행)

비밀번호는 이 스크립트가 절대 다루지 않습니다 — 뜨는 브라우저 창에 사용자가
직접 로그인하고, 그 "로그인된 상태"만 파일로 저장해서 나중에 자동화가
재사용합니다. 세션 파일(data/wing_session.json)에는 로그인 쿠키가 들어있으니
비밀번호처럼 다른 사람과 공유하지 마세요.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

SESSION_PATH = Path(__file__).resolve().parent / "data" / "wing_session.json"


def main() -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("WING 로그인 세션 저장 도구")
    print("=" * 60)
    print("1. 잠시 후 브라우저 창이 새로 열립니다.")
    print("2. 그 창에서 직접 WING에 로그인해주세요.")
    print("   (비밀번호 변경 요청 화면이 뜨면 이전에 안내드린 방법대로 처리해주세요)")
    print("3. 로그인 후 '상품관리 > 상품등록 > 카탈로그 매칭하기' 화면까지 한 번 들어가 보세요.")
    print("4. 다 되셨으면 이 창(터미널)으로 돌아와서 Enter를 눌러주세요.")
    print()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://wing.coupang.com")

        input("로그인 및 카탈로그 매칭 화면 이동을 마치셨으면 Enter를 눌러주세요... ")

        current_url = page.url
        context.storage_state(path=str(SESSION_PATH))
        browser.close()

    print()
    print(f"저장 완료: {SESSION_PATH}")
    print(f"마지막으로 보고 계셨던 페이지 주소:\n  {current_url}")
    print()
    print("이 주소를 그대로 복사해서 Claude에게 알려주세요 (카탈로그 매칭 화면을 자동으로 찾기 위해 필요합니다).")


if __name__ == "__main__":
    main()
