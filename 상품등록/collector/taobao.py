# ============================================================
# collector/taobao.py
# 타오바오 상품 상세페이지 1개를 열어 필요한 정보를 추출합니다.
# ------------------------------------------------------------
# 방식:
#   - Playwright 의 "고정 프로필(persistent context)" 로 브라우저를 띄웁니다.
#     → 처음 한 번 직접 로그인해두면 다음부터 로그인이 유지됩니다.
#   - 화면이 보이는 모드(headful) 로 띄웁니다. (타오바오는 headless 를 잘 막음)
#   - 요청 사이에 랜덤 딜레이를 넣습니다.
#   - 캡차/로그인월이 감지되면 자동으로 뚫지 않고 예외를 던지고 로그를 남깁니다.
#     (캡차 풀기·로그인은 사용자가 직접 처리해야 합니다.)
#
# 주의: 타오바오 페이지 구조(HTML 셀렉터)는 자주 바뀝니다.
#       아래 셀렉터는 "되는 만큼 추출, 실패하면 None" 전략이고,
#       첫 실전 수집 후 실제 페이지에 맞게 다듬어야 할 수 있습니다.
# ============================================================

import logging
import os
import random
import re
import time
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

logger = logging.getLogger("taobao_collector")

# 브라우저 프로필(로그인 유지)을 저장할 폴더. .gitignore 에 포함됨.
DEFAULT_PROFILE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".browser_profile"
)


class CaptchaDetected(Exception):
    """캡차/로그인월이 감지되었을 때 던지는 예외 (자동 우회하지 않음)."""


def _sleep_random(a: float = 3.0, b: float = 7.0) -> None:
    """요청 사이에 사람처럼 랜덤하게 쉽니다 (계정 동결/차단 방지)."""
    delay = random.uniform(a, b)
    logger.debug("랜덤 딜레이 %.1f초", delay)
    time.sleep(delay)


def _human_scroll(page, rounds: tuple[int, int] = (2, 4)) -> None:
    """사람처럼 천천히 스크롤해 봇 탐지를 줄입니다."""
    try:
        for _ in range(random.randint(*rounds)):
            page.mouse.wheel(0, random.randint(300, 800))
            time.sleep(random.uniform(0.8, 1.8))
    except Exception:
        pass


def _extract_item_id(url: str) -> str | None:
    """상세 URL 에서 상품 ID(id= 파라미터)를 뽑습니다."""
    try:
        qs = parse_qs(urlparse(url).query)
        if "id" in qs:
            return qs["id"][0]
    except Exception:
        pass
    # 쿼리에 없으면 경로에서 숫자 덩어리를 시도
    m = re.search(r"(\d{8,})", url)
    return m.group(1) if m else None


def _looks_like_captcha(page) -> bool:
    """현재 페이지가 캡차/로그인 요구 화면인지 대략 판단합니다."""
    url = (page.url or "").lower()
    # 타오바오의 캡차/차단/로그인 관련 URL 패턴
    if any(k in url for k in ("login", "sec.taobao", "_____tmd_____", "punish", "captcha")):
        return True
    # 슬라이더 캡차 등 대표적인 요소가 있으면 캡차로 봄
    for sel in ("#nc_1_wrapper", ".nc-container", "#J_MIDDLEWARE_FRAME_WIDGET", "text=滑动"):
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            continue
    return False


def _text_or_none(page, selectors: list[str]) -> str | None:
    """여러 셀렉터를 순서대로 시도해 첫 번째로 잡히는 텍스트를 돌려줍니다."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                txt = (loc.inner_text(timeout=1500) or "").strip()
                if txt:
                    return txt
        except Exception:
            continue
    return None


def _meta_content(page, prop: str) -> str | None:
    """<meta property=...> 또는 <meta name=...> 의 content 를 읽습니다."""
    for attr in ("property", "name"):
        try:
            loc = page.locator(f'meta[{attr}="{prop}"]').first
            if loc.count() > 0:
                val = loc.get_attribute("content")
                if val:
                    return val.strip()
        except Exception:
            continue
    return None


def _parse_price(text: str | None) -> float | None:
    """가격 문자열에서 숫자만 뽑아 float 로 바꿉니다. (예: '¥ 39.90' → 39.9)"""
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)", text.replace(",", ""))
    return float(m.group(1)) if m else None


def _parse_int(text: str | None) -> int | None:
    """'月销 1234' 같은 문자열에서 정수만 뽑습니다. (만 단위 표기는 근사 처리)"""
    if not text:
        return None
    t = text.replace(",", "")
    # 중국어 '万'(만) 처리: "2.3万" → 23000
    m = re.search(r"(\d+(?:\.\d+)?)\s*万", t)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r"(\d+)", t)
    return int(m.group(1)) if m else None


def _extract_options(page) -> dict | None:
    """색상/사이즈 등 SKU 옵션을 { 속성명: [값들] } 형태로 추출합니다(가능한 만큼)."""
    options: dict[str, list[str]] = {}
    try:
        groups = page.locator(".tb-sku dl, .skuCore dl, [class*='SkuContent'] [class*='valueItemText']")
        # 대표적인 타오바오/티몰 구조: dl 안에 dt(속성명) + dd>ul>li(값)
        dls = page.locator(".tb-sku dl")
        count = dls.count()
        for i in range(count):
            dl = dls.nth(i)
            name = (dl.locator("dt").first.inner_text(timeout=1000) or "").strip()
            values = []
            items = dl.locator("dd li")
            for j in range(items.count()):
                v = (items.nth(j).inner_text(timeout=1000) or "").strip()
                if v:
                    values.append(v)
            if name and values:
                options[name] = values
    except Exception as e:
        logger.debug("옵션 추출 중 무시된 오류: %s", e)
    return options or None


def _extract_images(page) -> list[str] | None:
    """상세페이지의 이미지 URL 들을 모읍니다(썸네일 갤러리 + 대표 이미지)."""
    urls: list[str] = []
    try:
        imgs = page.locator("#J_UlThumb img, [class*='thumbnail'] img, [class*='PicGallery'] img")
        for i in range(min(imgs.count(), 20)):
            src = imgs.nth(i).get_attribute("src") or imgs.nth(i).get_attribute("data-src")
            if src:
                if src.startswith("//"):
                    src = "https:" + src
                # 썸네일 사이즈 접미사(_50x50.jpg 등) 제거해 원본에 가깝게
                src = re.sub(r"_\d+x\d+\.(jpg|png|webp)", r".\1", src)
                if src not in urls:
                    urls.append(src)
    except Exception as e:
        logger.debug("이미지 추출 중 무시된 오류: %s", e)
    # 대표 이미지(og:image) 보강
    og = _meta_content(page, "og:image")
    if og:
        if og.startswith("//"):
            og = "https:" + og
        if og not in urls:
            urls.insert(0, og)
    return urls or None


def collect_taobao(url: str, profile_dir: str | None = None, headless: bool = False) -> dict:
    """
    타오바오 상세 URL 하나를 열어 상품 정보를 추출해 dict 로 돌려줍니다.
    캡차/로그인월이 뜨면 CaptchaDetected 예외를 던집니다.
    """
    profile_dir = profile_dir or DEFAULT_PROFILE_DIR
    os.makedirs(profile_dir, exist_ok=True)

    with sync_playwright() as p:
        # 고정 프로필로 브라우저 실행 → 로그인 상태가 유지됩니다.
        context = p.chromium.launch_persistent_context(
            profile_dir,
            headless=headless,
            locale="zh-CN",
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],  # 자동화 표시 최소화
        )
        page = context.new_page()
        try:
            logger.info("페이지 여는 중: %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            _sleep_random()

            # 캡차/로그인 감지 → 사용자에게 넘김 (자동 우회 안 함)
            if _looks_like_captcha(page):
                logger.warning("캡차/로그인월 감지됨: %s", page.url)
                raise CaptchaDetected(
                    "타오바오 캡차 또는 로그인 화면이 떴습니다. "
                    "열린 브라우저 창에서 직접 로그인/캡차를 해결한 뒤 다시 실행해주세요."
                )

            # 본문 로딩을 조금 기다림(가격/옵션이 늦게 뜨는 경우)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PWTimeoutError:
                pass

            # 사람처럼 천천히 스크롤(차단 방지)
            _human_scroll(page)

            title = _text_or_none(
                page, ["h1", ".tb-main-title", "[class*='ItemTitle']", "#J_Title .tb-main-title"]
            ) or _meta_content(page, "og:title")

            price_text = _text_or_none(
                page,
                [".tb-rmb-num", "[class*='Price--priceText']", "[class*='price'] .tb-rmb-num",
                 "em.tb-rmb-num", "[class*='originPrice']"],
            )
            shop_name = _text_or_none(
                page, [".tb-shop-name", "[class*='ShopName']", ".slogo-shopname"]
            )
            sales_text = _text_or_none(
                page, [".tb-sell-counter", "[class*='sellCount']", "text=/月销/"]
            )
            review_text = _text_or_none(
                page, ["#J_RateCounter", "[class*='comment'] [class*='count']", "text=/累计评价/"]
            )

            data = {
                "source_platform": "taobao",
                "source_url": url,
                "source_item_id": _extract_item_id(url),
                "title_original": title,
                "price_original": _parse_price(price_text),
                "currency": "CNY",
                "options": _extract_options(page),
                "image_urls": _extract_images(page),
                "sales_count": _parse_int(sales_text),
                "review_count": _parse_int(review_text),
                "shop_name": shop_name,
                # 파싱이 부실할 때 대비해 원본 흔적을 남겨둡니다.
                "raw_payload": {
                    "final_url": page.url,
                    "page_title": page.title(),
                    "price_text": price_text,
                    "sales_text": sales_text,
                    "review_text": review_text,
                },
            }
            _sleep_random(1.0, 2.5)
            logger.info("추출 완료: %s", data.get("title_original"))
            return data
        finally:
            context.close()
