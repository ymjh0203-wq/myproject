# ==========================================================
# 쿠팡 WING 로그인 자동화 (integrations/coupang_wing_automation.py)
# ----------------------------------------------------------
# 공식 API 대신 '로그인(자동화)' 방식으로 쿠팡 판매자센터(WING)를 자동화하기 위한
# 1단계 모듈입니다. 핵심은 '로그인이 자동화로 유지되는가(2차인증·세션)'를 먼저 검증.
#
# 방식(퀵스타 자동화와 동일): 전용 크롬 프로필에 사장님이 '직접 1회 로그인'(2차인증
# 포함)하면 그 세션이 프로필에 저장되어, 다음부터는 로그인 없이 자동으로 WING에 접근.
#   → WING 비밀번호를 앱에 저장하지 않습니다(안전). 2차인증도 사람이 직접 통과.
#
# 사용(명령 프롬프트에서, 이 폴더 안):
#   .venv\Scripts\python -m integrations.coupang_wing_automation login           # 최초 1회 로그인(창 뜸)
#   .venv\Scripts\python -m integrations.coupang_wing_automation check           # 로그인 상태 확인
#   .venv\Scripts\python -m integrations.coupang_wing_automation login 2호점     # 계정별 프로필(선택)
# ==========================================================

import os
import sys
import time

WING_HOME = "https://wing.coupang.com/"


def _profile_dir(account_key: str = "") -> str:
    """계정별 전용 크롬 프로필 폴더. account_key를 주면 상점마다 세션을 따로 저장합니다."""
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    suffix = ("_" + "".join(c for c in account_key if c.isalnum() or c in "-_")) if account_key else ""
    return os.path.join(base, "OrderManagementApp_CoupangWingBot" + suffix)


class CoupangWingAutomationError(Exception):
    pass


def _clear_profile_locks(profile_dir: str) -> None:
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(profile_dir, lock))
        except OSError:
            pass


def _launch(account_key: str, headless: bool):
    """전용 프로필로 크롬을 띄웁니다(playwright 컨텍스트 반환)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise CoupangWingAutomationError(
            "playwright가 설치되어 있지 않습니다. '.venv\\Scripts\\python -m pip install playwright' 로 설치하세요."
        )
    profile_dir = _profile_dir(account_key)
    os.makedirs(profile_dir, exist_ok=True)
    _clear_profile_locks(profile_dir)
    pw = sync_playwright().start()
    try:
        context = pw.chromium.launch_persistent_context(
            profile_dir,
            channel="chrome",
            headless=headless,
            args=["--start-maximized"],
            no_viewport=True,
        )
    except Exception as error:
        pw.stop()
        raise CoupangWingAutomationError(f"크롬을 띄우지 못했습니다: {error}")
    return pw, context


def _looks_logged_in(page) -> bool:
    """현재 페이지가 'WING 로그인 완료' 상태로 보이는지 휴리스틱으로 판단합니다.
    - 주소가 쿠팡 로그인/인증 호스트가 아니라 wing.coupang.com 안이고,
    - 비밀번호 입력칸이 없으면(로그인 화면이 아니면) 로그인된 것으로 봅니다."""
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    if "wing.coupang.com" not in url:
        return False
    if "login" in url or "xauth" in url or "sso" in url:
        return False
    try:
        # 로그인 폼이면 password 입력칸이 있습니다.
        if page.query_selector("input[type=password]"):
            return False
    except Exception:
        pass
    return True


def open_for_login(account_key: str = "") -> None:
    """(최초 1회) WING 로그인용 크롬 창을 띄웁니다. 사장님이 이 창에서 직접 로그인
    (2차인증 포함)하면, 로그인된 것을 감지해 알리고, 로그인 후 머문 화면(주문목록 등)의
    HTML을 저장해 다음 단계(주문 긁기) 구조 분석에 씁니다."""
    profile_dir = _profile_dir(account_key)
    pw, context = _launch(account_key, headless=False)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(WING_HOME, timeout=60000)
        print("이 창에서 쿠팡 WING에 로그인하세요. (2차인증이 있으면 직접 통과)", flush=True)
        print("로그인 후, 보고 싶은 '주문 목록' 화면까지 이동해 두세요. 자동으로 감지합니다.", flush=True)

        deadline = time.time() + 600  # 최대 10분
        detected = False
        stable = 0
        while time.time() < deadline:
            try:
                if page.is_closed():
                    break
                if _looks_logged_in(page):
                    stable += 1
                    if stable >= 2:  # 두 번 연속 로그인 상태면 확정(리다이렉트 중간 상태 배제)
                        detected = True
                        break
                else:
                    stable = 0
            except Exception:
                pass
            time.sleep(4)

        if not detected:
            print("NOT_DETECTED (로그인이 감지되지 않았습니다)", flush=True)
            return

        print("LOGGED_IN", flush=True)
        # 사장님이 주문목록까지 이동할 시간을 조금 더 준 뒤, 현재 화면을 저장합니다.
        print("현재 화면을 15초 뒤에 저장합니다. 주문 목록 화면을 띄워두세요…", flush=True)
        time.sleep(15)
        try:
            html_path = os.path.join(profile_dir, "wing_current.html")
            with open(html_path, "w", encoding="utf-8") as fp:
                fp.write(page.content())
            print(f"SAVED url={page.url}", flush=True)
            print(f"SAVED_FILE {html_path}", flush=True)
        except Exception as error:
            print(f"DUMP_FAILED: {error}", flush=True)
    except Exception as error:
        print(f"ERROR: {error}", flush=True)
    finally:
        try:
            context.close()
        except Exception:
            pass
        pw.stop()


def _active_page(context):
    """현재 사용자가 보고 있는 페이지(가장 마지막으로 연/활성 탭)를 고릅니다.
    about:blank·빈 탭은 피하고, wing.coupang.com 페이지를 우선합니다."""
    pages = [p for p in context.pages if not p.is_closed()]
    if not pages:
        return None
    wing = [p for p in pages if "wing.coupang.com" in (p.url or "")]
    pool = wing or [p for p in pages if (p.url or "about:blank") != "about:blank"] or pages
    return pool[-1]


def grab(account_key: str = "", wait_seconds: int = 45) -> None:
    """저장된 세션으로 WING을 '창'으로 띄웁니다(이미 로그인돼 있으면 로그인 불필요).
    사장님이 '주문 목록' 화면까지 이동하면, wait_seconds 뒤 그 화면을 HTML+스크린샷으로
    저장합니다. (로그인 자동 감지에 의존하지 않아 가장 확실한 캡처 방법)"""
    profile_dir = _profile_dir(account_key)
    pw, context = _launch(account_key, headless=False)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(WING_HOME, timeout=60000)
        print("WING 창이 열렸습니다.", flush=True)
        print("로그인이 안 돼 있으면 로그인(2차인증 포함)하고, '주문 목록' 화면까지 이동하세요.", flush=True)
        print(f"{wait_seconds}초 뒤 현재 보고 있는 화면을 자동 저장합니다…", flush=True)
        # 진행 상황(현재 URL) 몇 번 찍어줍니다.
        for i in range(wait_seconds, 0, -5):
            time.sleep(5)
            ap = _active_page(context)
            try:
                print(f"  남은 {i - 5}s · 현재: {ap.url if ap else '?'}", flush=True)
            except Exception:
                pass
        ap = _active_page(context) or page
        try:
            html_path = os.path.join(profile_dir, "wing_current.html")
            png_path = os.path.join(profile_dir, "wing_current.png")
            with open(html_path, "w", encoding="utf-8") as fp:
                fp.write(ap.content())
            try:
                ap.screenshot(path=png_path, full_page=True)
            except Exception:
                pass
            print(f"SAVED url={ap.url}", flush=True)
            print(f"SAVED_FILE {html_path}", flush=True)
            print(f"SAVED_PNG {png_path}", flush=True)
        except Exception as error:
            print(f"DUMP_FAILED: {error}", flush=True)
    except Exception as error:
        print(f"ERROR: {error}", flush=True)
    finally:
        try:
            context.close()
        except Exception:
            pass
        pw.stop()


def is_logged_in(account_key: str = "") -> bool:
    """전용 프로필이 WING에 로그인된 상태인지 (창 없이) 확인합니다."""
    pw, context = _launch(account_key, headless=True)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(WING_HOME, timeout=60000)
        time.sleep(3)
        return _looks_logged_in(page)
    finally:
        try:
            context.close()
        except Exception:
            pass
        pw.stop()


if __name__ == "__main__":
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "login").strip().lower()
    acct = sys.argv[2] if len(sys.argv) > 2 else ""
    if cmd == "login":
        open_for_login(acct)
    elif cmd == "grab":
        grab(acct)
    elif cmd == "check":
        print("LOGGED_IN" if is_logged_in(acct) else "NOT_LOGGED_IN", flush=True)
    else:
        print("사용법: python -m integrations.coupang_wing_automation [login|grab|check] [계정키]", flush=True)
