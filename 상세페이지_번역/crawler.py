# ==========================================================
# 상세페이지 이미지 수집 (crawler.py)
# ----------------------------------------------------------
# 쿠팡 / 네이버 스마트스토어 같은 국내 오픈마켓 상품 URL을 받아서, 그 상품 페이지에 있는
# 이미지들을 "상세페이지 이미지"와 "옵션(색상/사이즈 등) 이미지"로 나눠서 찾아내고 다운로드합니다.
#
# (원래는 타오바오/1688 원본 사이트를 직접 크롤링했지만, 계정 동결 위험 때문에
# 국내 오픈마켓에 이미 올라와 있는 상품 상세이미지를 가져오는 방식으로 바꿨습니다.
# 국내 오픈마켓은 상품 상세보기에 로그인이 필요 없어서 로그인 세션 관리가 필요 없습니다.)
#
# 이 사이트들도 자바스크립트로 화면을 그리는 경우가 많아서, 단순히 requests로
# HTML만 가져오면 실패하는 경우가 있습니다. 그래서 실제 브라우저를 띄우는
# Playwright를 사용합니다.
#
# 사이트 구조는 자주 바뀔 수 있어서, 여기서 실패해도 프로그램 전체가 죽지 않고
# CrawlError로 이유를 알려줘서 화면에서 "직접 업로드" 쪽을 안내할 수 있게 합니다.
# ==========================================================

import io
import os
import time
from urllib.parse import quote, urljoin

import requests
from PIL import Image
from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 상세설명 영역을 찾기 위해 순서대로 시도해보는 CSS 선택자들입니다.
# 사이트(쿠팡, 네이버 스마트스토어 등)마다 구조가 달라서 여러 개를 시도합니다.
DESCRIPTION_SELECTORS = [
    # 네이버 스마트스토어 (스마트에디터로 만든 상세설명)
    ".se-main-container img",
    ".se-image-resource",
    # 쿠팡
    "#productDetail img",
    ".product-detail-content img",
    ".prod-detail-content img",
    "[class*='vendor-item-detail'] img",
    # 그 외 일반적인 이름들
    "#description img",
    ".detail-desc img",
    "[class*='detail-content'] img",
    "[class*='detail-desc'] img",
    "[id*='detail'] img",
    "[class*='detail'] img",
    "[id*='desc'] img",
    "[class*='desc'] img",
]

MIN_IMAGE_WIDTH = 300  # 이보다 작으면 아이콘/로고로 보고 제외
LOAD_TIMEOUT_MS = 30000
MAX_SCROLL_STEPS = 12

# 상품 옵션(색상/사이즈 등) 선택용 이미지, 상단 대표 이미지 갤러리에 흔히 쓰이는 class/id
# 이름 키워드입니다. 상세페이지 이미지는 아니지만, 사용자가 참고용으로 받아볼 수 있게
# "옵션 이미지"로 따로 분류합니다 (버리지 않습니다).
OPTION_KEYWORDS = ["sku", "prop", "option", "thumb", "gallery", "carousel", "swiper"]

# 이 키워드에 해당하는 이미지는 상세페이지도 옵션도 아닌 순수 잡음(로그인, 장바구니, 리뷰,
# 추천상품, 메뉴 등)이라서 상세/옵션 어느 쪽으로도 분류하지 않고 완전히 버립니다.
NOISE_KEYWORDS = [
    "banner", "recommend", "guess", "similar", "related", "review", "comment", "rate",
    "header", "footer", "nav", "login", "qrcode", "cart", "service", "coupon",
    "shop-info", "seller", "search", "gnb", "lnb",
]


APP_URL = "http://localhost:8502/"


def build_browser_console_script() -> str:
    """사용자가 실제 브라우저(F12 개발자도구 콘솔, 또는 즐겨찾기)에서 직접 실행할 스크립트를 만들어 반환합니다.

    쿠팡처럼 자동화 접속 자체를 차단하는 사이트에서는, 서버가 대신 접속하는 방식(Playwright)이
    통하지 않습니다. 대신 사용자가 이미 정상적으로 열어본 실제 페이지에서 이미지 주소만
    뽑아내는 방식을 씁니다 (자동화가 아니라 사용자 본인의 브라우저이므로 차단되지 않습니다).
    분류 기준은 OPTION_KEYWORDS / NOISE_KEYWORDS / MIN_IMAGE_WIDTH와 동일하게 맞춥니다.
    상세페이지 이미지는 "D:", 옵션 이미지는 "O:"를 앞에 붙여서 한 목록으로 넘깁니다.

    찾은 주소들은, 복사/붙여넣기 없이 이 프로그램(APP_URL)을 새 탭으로 열면서 그대로 넘겨줘서
    사람이 직접 복사-전환-붙여넣기 하는 수고를 덜어줍니다. 팝업이 차단된 경우에만 예전처럼
    prompt() 창(Ctrl+C로 복사)으로 대신 보여줍니다.
    """
    option_words_js = ", ".join(f"'{w}'" for w in OPTION_KEYWORDS)
    noise_words_js = ", ".join(f"'{w}'" for w in NOISE_KEYWORDS)
    return f"""(function(){{
  const optionWords = [{option_words_js}];
  const noiseWords = [{noise_words_js}];
  const detailFound = [];
  const optionFound = [];
  document.querySelectorAll('img').forEach(img => {{
    if ((img.naturalWidth || img.width || 0) < {MIN_IMAGE_WIDTH}) return;
    let el = img, ctxAll = '', depth = 0;
    while (el && depth < 6) {{
      ctxAll += ' ' + ((el.id || '') + ' ' + (el.className || '')).toString().toLowerCase();
      el = el.parentElement; depth++;
    }}
    let category = 'detail';
    if (noiseWords.some(w => ctxAll.includes(w))) category = 'noise';
    else if (optionWords.some(w => ctxAll.includes(w))) category = 'option';
    if (category === 'noise') return;
    const src = img.currentSrc || img.src;
    if (!src || src.startsWith('data:')) return;
    (category === 'option' ? optionFound : detailFound).push(src);
  }});
  const uniqueDetail = [...new Set(detailFound)];
  const uniqueOption = [...new Set(optionFound)];
  const lines = uniqueDetail.map(u => 'D:' + u).concat(uniqueOption.map(u => 'O:' + u));
  const text = lines.join('\\n');
  const appUrl = '{APP_URL}?paste=' + encodeURIComponent(text);
  let opened = null;
  try {{ opened = window.open(appUrl, '_blank'); }} catch (e) {{ opened = null; }}
  if (!opened) {{
    // 새 탭이 차단되었거나(팝업 차단), 프로그램 창 주소가 달라서 못 열었을 때를 대비한 예전 방식입니다.
    // 개발자도구 콘솔에서 실행하면 navigator.clipboard.writeText가 보안 정책 때문에
    // 조용히 실패하는 경우가 많아서, 클립보드 자동 복사 대신 항상 팝업창으로 보여줍니다.
    prompt(uniqueDetail.length + '개의 상세페이지 이미지, ' + uniqueOption.length + '개의 옵션 이미지를 찾았습니다. Ctrl+C(맥: Cmd+C)로 복사한 뒤 확인을 누르세요:', text);
  }}
}})();"""


def build_bookmarklet_href() -> str:
    """위 콘솔 스크립트를 "즐겨찾기(북마클릿)"으로 쓸 수 있는 javascript: 링크로 만들어줍니다.

    개발자도구 콘솔을 여는 것보다, 즐겨찾기 바에 한 번 등록해두고 클릭만 하는 쪽이
    비개발자에게 훨씬 쉽습니다. 브라우저 주소창에는 javascript: 링크를 직접 붙여넣기 힘든
    경우가 많아서(붙여넣기 시 자동으로 지워짐), 즐겨찾기 바로 "드래그해서 등록"하는 방식을 씁니다.
    """
    script = build_browser_console_script()
    return "javascript:" + quote(script)


class CrawlError(Exception):
    """크롤링이 실패했을 때, 사용자에게 보여줄 한국어 메시지를 담습니다.

    가능하면 실패 시점에 크롤러가 실제로 보고 있던 화면(스크린샷, 페이지 제목)도
    함께 담아서, 화면 구조 문제인지 접속 차단인지 사용자가 바로 눈으로 확인할 수 있게 합니다.
    """

    def __init__(self, message, screenshot_bytes: bytes | None = None, page_title: str | None = None):
        super().__init__(message)
        self.screenshot_bytes = screenshot_bytes
        self.page_title = page_title


def _collect_images_from_frame(frame):
    urls = []
    for selector in DESCRIPTION_SELECTORS:
        try:
            handles = frame.query_selector_all(selector)
        except Exception:
            handles = []
        if not handles:
            continue
        for handle in handles:
            urls.extend(_extract_urls(handle))
        if urls:
            # 상세설명 선택자 중 하나가 이미지를 찾았다면 그걸로 충분합니다.
            return urls
    return urls


def _extract_urls(handle):
    found = []
    for attr in ("src", "data-src", "data-lazy-src", "data-original"):
        value = handle.get_attribute(attr)
        if value and not value.startswith("data:"):
            found.append(value)
            break
    return found


_ANCESTOR_CONTEXT_JS = """(img) => {
    let out = [];
    let el = img.parentElement;
    for (let k = 0; k < 6 && el; k++) {
        out.push((el.id || '') + ' ' + (el.className || '').toString());
        el = el.parentElement;
    }
    return out.join(' ');
}"""


def _classify_by_ancestor(frame, handle) -> str:
    """이미지의 조상 요소 class/id를 보고 "detail" / "option" / "noise" 중 하나로 분류합니다."""
    try:
        context_text = frame.evaluate(_ANCESTOR_CONTEXT_JS, handle).lower()
    except Exception:
        return "detail"
    if any(keyword in context_text for keyword in NOISE_KEYWORDS):
        return "noise"
    if any(keyword in context_text for keyword in OPTION_KEYWORDS):
        return "option"
    return "detail"


def _fallback_collect_and_classify(frame):
    """상세설명 선택자로 아무것도 못 찾았을 때, 페이지 전체에서 큰 이미지를 찾아
    상세페이지 이미지와 옵션 이미지로 나눠 담습니다. (detail_urls, option_urls) 튜플을 반환합니다.
    """
    try:
        handles = frame.query_selector_all("img")
    except Exception:
        return [], []
    detail_urls = []
    option_urls = []
    for handle in handles:
        try:
            natural_width = frame.evaluate("(img) => img.naturalWidth", handle)
        except Exception:
            natural_width = 0
        if natural_width and natural_width < MIN_IMAGE_WIDTH:
            continue
        category = _classify_by_ancestor(frame, handle)
        if category == "noise":
            continue
        urls = _extract_urls(handle)
        if category == "option":
            option_urls.extend(urls)
        else:
            detail_urls.extend(urls)
    return detail_urls, option_urls


def _dedupe_keep_order(items):
    seen = set()
    ordered = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def fetch_detail_images(product_url: str) -> dict:
    """상품 URL(쿠팡, 네이버 스마트스토어 등)에서 이미지 주소를 찾아
    {"detail": [...], "option": [...]} 형태로 반환합니다.

    아무것도 찾지 못했거나 접속이 막히면 CrawlError를 발생시킵니다.
    """
    product_url = product_url.strip()
    if not product_url:
        raise CrawlError("상품 URL을 입력해주세요.")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1280, "height": 1600})
                page.set_default_timeout(LOAD_TIMEOUT_MS)
                try:
                    page.goto(product_url, wait_until="load", timeout=LOAD_TIMEOUT_MS)
                except Exception as exc:
                    raise CrawlError(
                        "페이지를 여는 데 실패했습니다. URL이 올바른지, 인터넷 연결이 되는지 확인해주세요."
                    ) from exc

                # 상세설명 이미지는 스크롤을 내려야 (지연 로딩) 실제 주소가 채워지는
                # 경우가 많아서, 아래로 여러 번 나눠서 스크롤합니다.
                for _ in range(MAX_SCROLL_STEPS):
                    page.mouse.wheel(0, 2000)
                    time.sleep(0.4)

                frames_to_check = [page.main_frame] + list(page.frames)

                detail_collected = []
                for frame in frames_to_check:
                    detail_collected.extend(_collect_images_from_frame(frame))
                    if detail_collected:
                        break

                option_collected = []
                if not detail_collected:
                    # 상세설명 전용 선택자로 못 찾았을 때는, 페이지 전체를 훑어서
                    # 상세/옵션을 함께 분류합니다.
                    for frame in frames_to_check:
                        d, o = _fallback_collect_and_classify(frame)
                        detail_collected.extend(d)
                        option_collected.extend(o)
                else:
                    # 상세설명은 선택자로 찾았더라도, 옵션 이미지는 별도로 한 번 더 훑어야 합니다.
                    for frame in frames_to_check:
                        _, o = _fallback_collect_and_classify(frame)
                        option_collected.extend(o)

                detail_urls = [urljoin(product_url, u) for u in _dedupe_keep_order(detail_collected)]
                option_urls = [urljoin(product_url, u) for u in _dedupe_keep_order(option_collected)]

                if not detail_urls and not option_urls:
                    screenshot_bytes = None
                    page_title = None
                    try:
                        screenshot_bytes = page.screenshot(full_page=False)
                    except Exception:
                        pass
                    try:
                        page_title = page.title()
                    except Exception:
                        pass
                    raise CrawlError(
                        "상세페이지에서 이미지를 찾지 못했습니다. "
                        "사이트 구조가 달라졌거나 상세설명이 다른 방식으로 되어 있을 수 있습니다. "
                        "'직접 업로드' 탭을 이용해주세요.",
                        screenshot_bytes=screenshot_bytes,
                        page_title=page_title,
                    )
                return {"detail": detail_urls, "option": option_urls}
            finally:
                browser.close()
    except CrawlError:
        raise
    except Exception as exc:
        raise CrawlError(
            "크롤링 중 알 수 없는 오류가 발생했습니다. "
            "사이트가 접속을 막았을 수 있습니다. '직접 업로드' 탭을 이용해주세요."
        ) from exc


def download_images(image_urls: list[str], referer: str, out_dir: str, prefix: str = "source") -> list[str]:
    """이미지 주소 목록을 실제 파일로 내려받아 out_dir에 저장하고, 로컬 파일 경로 목록을 반환합니다.

    낱개 이미지가 실패해도 나머지는 계속 진행하고, 실패한 이미지는 건너뜁니다.
    """
    os.makedirs(out_dir, exist_ok=True)
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer

    saved_paths = []
    for index, url in enumerate(image_urls, start=1):
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
        except Exception:
            continue

        # SVG나 깨진 파일처럼 실제로는 그림으로 열 수 없는 파일이 섞여 들어오면
        # 나중에 화면에 표시하다가 프로그램이 죽을 수 있어서, 여기서 미리 걸러냅니다.
        try:
            Image.open(io.BytesIO(resp.content)).verify()
        except Exception:
            continue

        ext = ".jpg"
        content_type = resp.headers.get("Content-Type", "")
        if "png" in content_type:
            ext = ".png"
        elif "webp" in content_type:
            ext = ".webp"

        file_path = os.path.join(out_dir, f"{prefix}_{index:02d}{ext}")
        with open(file_path, "wb") as f:
            f.write(resp.content)
        saved_paths.append(file_path)

    if not saved_paths:
        raise CrawlError("이미지 주소는 찾았지만 다운로드에 모두 실패했습니다. '직접 업로드' 탭을 이용해주세요.")

    return saved_paths
