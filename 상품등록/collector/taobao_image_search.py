# ============================================================
# collector/taobao_image_search.py
# 타오바오 이미지검색(拍立淘)으로 유사 상품 후보 URL 을 찾습니다.
# ------------------------------------------------------------
# 방식:
#   - login_setup.py 로 만든 고정 프로필(로그인 유지) 을 재사용합니다.
#   - 이미지 파일을 업로드해 검색하고, 결과에서 상품 상세 URL 을 N개 모읍니다.
#   - 캡차/차단이 뜨면 자동으로 뚫지 않습니다.
#       * manual=False: CaptchaDetected 예외로 알림
#       * manual=True : 보이는 창에서 사용자가 직접 검색을 끝내도록 기다린 뒤
#                       현재 결과 페이지에서 링크를 긁습니다(폴백).
#
# 주의: 타오바오 검색결과 페이지 구조는 자주 바뀌고 봇차단이 강합니다.
#       아래 셀렉터/흐름은 첫 실전 후 실제 페이지에 맞게 다듬어야 할 수 있습니다.
# ============================================================

import logging
import re

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

# taobao.py 의 공통 유틸(프로필 경로, 캡차 감지, 랜덤 딜레이, 예외)을 재사용
from collector.taobao import (
    CaptchaDetected,
    DEFAULT_PROFILE_DIR,
    _looks_like_captcha,
    _sleep_random,
)

logger = logging.getLogger("taobao_image_search")

TAOBAO_HOME = "https://www.taobao.com"

# 이미지 업로드용 input[type=file] 후보 셀렉터 (버전에 따라 다름)
_FILE_INPUT_SELECTORS = [
    "input.search-img-upload",
    "input[type='file']",
    "#J_ImgSearchUpload input[type='file']",
]

# 이미지검색 카메라 아이콘 후보(파일 input 이 숨어있을 때 눌러서 노출)
_CAMERA_SELECTORS = [
    "#J_ImgSearch",
    ".icon-camera",
    "[class*='camera']",
    "[class*='imgSearch']",
]


def _collect_item_links(page, n: int) -> list[str]:
    """결과 페이지에서 상품 상세 URL 을 순서대로 최대 n개 모읍니다(중복 제거)."""
    links: list[str] = []
    anchors = page.locator("a[href*='item.taobao.com'], a[href*='detail.tmall.com']")
    total = anchors.count()
    for i in range(total):
        href = anchors.nth(i).get_attribute("href")
        if not href:
            continue
        if href.startswith("//"):
            href = "https:" + href
        # id 파라미터가 있는 진짜 상품 링크만
        if "id=" not in href:
            continue
        # 같은 상품(id) 중복 제거
        m = re.search(r"[?&]id=(\d+)", href)
        key = m.group(1) if m else href
        if all(key not in u for u in links):
            links.append(href)
        if len(links) >= n:
            break
    return links


def image_search(image_path: str, n: int = 3, profile_dir: str | None = None,
                 manual: bool = False) -> list[str]:
    """
    이미지 파일로 타오바오를 검색해 유사 상품 상세 URL 을 최대 n개 반환합니다.
    manual=True 면 사용자가 창에서 직접 검색을 끝낼 때까지 기다립니다.
    """
    profile_dir = profile_dir or DEFAULT_PROFILE_DIR

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            profile_dir,
            headless=False,   # 이미지검색은 항상 화면 보이게(캡차 대응)
            locale="zh-CN",
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            logger.info("타오바오 홈 여는 중...")
            page.goto(TAOBAO_HOME, wait_until="domcontentloaded", timeout=60000)
            _sleep_random()

            if manual:
                # 폴백 경로: 사람이 직접 카메라 아이콘으로 이미지 업로드/검색을 끝냄
                print("\n[수동 모드] 열린 타오바오 창에서 직접:")
                print(f"   1) 검색창의 카메라(이미지검색) 아이콘 클릭")
                print(f"   2) 이 이미지 업로드:  {image_path}")
                print("   3) 검색 결과가 보이면 이 터미널로 돌아와 Enter")
                input("결과 페이지가 뜨면 Enter를 눌러주세요... ")
            else:
                # 자동 경로: 숨은 file input 을 찾아 이미지 주입
                file_input = None
                for sel in _FILE_INPUT_SELECTORS:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        file_input = loc
                        break
                if file_input is None:
                    # 카메라 아이콘을 눌러 input 을 노출시켜 본다
                    for sel in _CAMERA_SELECTORS:
                        cam = page.locator(sel).first
                        if cam.count() > 0:
                            try:
                                cam.click(timeout=3000)
                                _sleep_random(1.0, 2.0)
                            except Exception:
                                pass
                            break
                    for sel in _FILE_INPUT_SELECTORS:
                        loc = page.locator(sel).first
                        if loc.count() > 0:
                            file_input = loc
                            break
                if file_input is None:
                    raise CaptchaDetected(
                        "이미지검색 업로드 칸을 찾지 못했습니다. "
                        "--manual 로 다시 실행해 창에서 직접 검색해주세요."
                    )

                logger.info("이미지 업로드 중: %s", image_path)
                file_input.set_input_files(image_path)
                _sleep_random(2.0, 4.0)

            # 결과 로딩 대기
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeoutError:
                pass

            # 캡차/차단 감지 (자동 모드에서만 예외; 수동 모드는 사용자가 이미 처리)
            if not manual and _looks_like_captcha(page):
                logger.warning("캡차/차단 감지됨: %s", page.url)
                raise CaptchaDetected(
                    "이미지검색 중 캡차/차단이 떴습니다. "
                    "--manual 로 다시 실행해 창에서 직접 검색을 끝내주세요."
                )

            links = _collect_item_links(page, n)
            logger.info("후보 상품 링크 %d개 수집", len(links))
            if not links:
                logger.warning(
                    "결과에서 상품 링크를 못 찾았습니다. 셀렉터 조정 또는 --manual 필요."
                )
            return links
        finally:
            context.close()
