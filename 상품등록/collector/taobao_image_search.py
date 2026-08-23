# ============================================================
# collector/taobao_image_search.py
# 타오바오 이미지검색(拍立淘)으로 유사 상품 후보 URL 을 찾습니다.
# ------------------------------------------------------------
# 방식:
#   - login_setup.py 로 만든 고정 프로필(로그인 유지) 을 재사용합니다.
#   - 이미지 파일을 업로드해 검색하고, 결과에서 상품 상세 URL 을 N개 모읍니다.
#   - 캡차가 뜨면 자동으로 뚫지 않고, 보이는 창에서 사용자가 직접 처리하는 동안
#     결과가 뜨기를 기다립니다(폴링).
#
# 진단: 잘 안 될 때를 대비해 seeds/ 폴더에 디버그 스크린샷을 남기고,
#       페이지 URL/제목/후보요소 개수를 로그로 남깁니다(원인 파악용).
# ============================================================

import logging
import os
import re
import time

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from collector.taobao import (
    CaptchaDetected,
    DEFAULT_PROFILE_DIR,
    _looks_like_captcha,
    _sleep_random,
)

logger = logging.getLogger("taobao_image_search")

TAOBAO_HOME = "https://www.taobao.com"
SEEDS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "seeds")

# 이미지 업로드용 input[type=file] 후보
_FILE_INPUT_SELECTORS = [
    "input[type='file']",
    "input.search-img-upload",
    "#J_ImgSearchUpload input[type='file']",
]

# 이미지검색 카메라 아이콘 후보(파일 input 이 숨어있을 때 눌러서 노출)
_CAMERA_SELECTORS = [
    "#J_ImgSearch",
    ".icon-camera",
    "[class*='camera']",
    "[class*='imgsearch' i]",
    "[class*='ImageSearch']",
    "[aria-label*='图片']",
    "[title*='图片']",
]


def _dump_debug(page, tag: str) -> str | None:
    """현재 페이지를 seeds/debug_{tag}.png 로 저장하고 경로를 반환(원인 파악용)."""
    try:
        os.makedirs(SEEDS_DIR, exist_ok=True)
        path = os.path.join(SEEDS_DIR, f"debug_{tag}.png")
        page.screenshot(path=path)
        logger.info("[진단] %s | url=%s | title=%s | shot=%s",
                    tag, page.url, page.title(), path)
        return path
    except Exception as e:
        logger.info("[진단] 스크린샷 실패(%s): %s", tag, e)
        return None


def _find_file_input(page):
    """페이지(및 iframe)에서 파일 업로드 input 을 찾습니다."""
    for sel in _FILE_INPUT_SELECTORS:
        loc = page.locator(sel).first
        try:
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def _collect_item_links(page, n: int) -> list[str]:
    """결과 페이지에서 상품 상세 URL 을 최대 n개 모읍니다(중복 제거)."""
    links: list[str] = []
    anchors = page.locator("a[href*='item.taobao.com'], a[href*='detail.tmall.com']")
    try:
        total = anchors.count()
    except Exception:
        return links
    for i in range(total):
        href = anchors.nth(i).get_attribute("href")
        if not href:
            continue
        if href.startswith("//"):
            href = "https:" + href
        if "id=" not in href:
            continue
        m = re.search(r"[?&]id=(\d+)", href)
        key = m.group(1) if m else href
        if all(key not in u for u in links):
            links.append(href)
        if len(links) >= n:
            break
    return links


def image_search(image_path: str, n: int = 3, profile_dir: str | None = None,
                 manual: bool = False, poll_seconds: int = 90) -> list[str]:
    """
    이미지 파일로 타오바오를 검색해 유사 상품 상세 URL 을 최대 n개 반환합니다.
    캡차가 뜨면 보이는 창에서 사용자가 직접 처리하고, 결과가 뜨면 자동 수집합니다.
    """
    profile_dir = profile_dir or DEFAULT_PROFILE_DIR

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            profile_dir,
            headless=False,
            locale="zh-CN",
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            logger.info("타오바오 홈 여는 중...")
            page.goto(TAOBAO_HOME, wait_until="domcontentloaded", timeout=60000)
            _sleep_random()
            _dump_debug(page, "01_home")

            uploaded = False
            if not manual:
                # 전략1: 이미 있는 hidden file input
                file_input = _find_file_input(page)
                # 전략2: 카메라 클릭 → file chooser 로 업로드
                if file_input is None:
                    for sel in _CAMERA_SELECTORS:
                        cam = page.locator(sel).first
                        try:
                            if cam.count() == 0:
                                continue
                            with page.expect_file_chooser(timeout=4000) as fc:
                                cam.click(timeout=3000)
                            fc.value.set_input_files(image_path)
                            uploaded = True
                            logger.info("[업로드] file chooser 방식 성공(%s)", sel)
                            break
                        except Exception:
                            # 클릭으로 hidden input 이 생겼을 수도 있으니 재탐색
                            file_input = _find_file_input(page)
                            if file_input is not None:
                                break
                            continue
                # 전략3: (전략1/2에서 찾은) file input 에 직접 주입
                if not uploaded and file_input is not None:
                    file_input.set_input_files(image_path)
                    uploaded = True
                    logger.info("[업로드] hidden input 방식 성공")

                if not uploaded:
                    _dump_debug(page, "02_no_upload")
                    raise CaptchaDetected(
                        "이미지검색 업로드 칸을 자동으로 찾지 못했습니다. "
                        "뜬 창에서 검색창의 카메라 아이콘으로 직접 이미지를 올려 검색해 주세요"
                        "(그러면 결과가 뜨는 대로 자동으로 이어받습니다)."
                    )
                _sleep_random(2.0, 4.0)

            # 결과가 나올 때까지 폴링(그 사이 캡차/수동 업로드는 창에서 처리)
            deadline = time.time() + poll_seconds
            links: list[str] = []
            while time.time() < deadline:
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except PWTimeoutError:
                    pass
                links = _collect_item_links(page, n)
                if links:
                    break
                _sleep_random(2.5, 4.0)

            logger.info("후보 상품 링크 %d개 수집", len(links))
            if not links:
                _dump_debug(page, "03_result_empty")
                if _looks_like_captcha(page):
                    raise CaptchaDetected(
                        "제한시간 안에 결과가 안 떴습니다(캡차/차단 가능). "
                        "열린 창에서 검색을 끝낸 뒤 다시 시도하거나 대기시간을 늘려주세요."
                    )
                logger.warning("결과에서 상품 링크를 못 찾았습니다(셀렉터 조정 필요할 수 있음).")
            return links
        finally:
            context.close()
