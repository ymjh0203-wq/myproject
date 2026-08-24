# ============================================================
# seed.py  —  벤치마킹 "씨앗 이미지" 확보
# ------------------------------------------------------------
# 두 가지 입력을 지원합니다:
#   1) 한국 마켓 상품 URL (스마트스토어/쿠팡 등)
#      → 페이지의 대표이미지(og:image 또는 메인 갤러리)를 찾아 로컬로 내려받음
#   2) 로컬 이미지 파일 경로
#      → 그대로 사용
#
# 확보한 이미지는 seeds/ 폴더에 저장하고, 타오바오 이미지검색에 사용합니다.
# ============================================================

import hashlib
import os
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SEEDS_DIR = os.path.join(BASE_DIR, "seeds")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _ensure_seeds_dir() -> None:
    os.makedirs(SEEDS_DIR, exist_ok=True)


def _download(image_url: str, referer: str) -> str:
    """이미지 URL을 seeds/ 폴더로 내려받고 로컬 경로를 반환합니다."""
    _ensure_seeds_dir()
    if image_url.startswith("//"):
        image_url = "https:" + image_url
    # 파일명은 URL 해시로 (중복/특수문자 문제 회피)
    ext = ".jpg"
    for e in (".jpg", ".jpeg", ".png", ".webp"):
        if e in image_url.lower():
            ext = e
            break
    name = hashlib.md5(image_url.encode("utf-8")).hexdigest()[:16] + ext
    path = os.path.join(SEEDS_DIR, name)

    req = urllib.request.Request(
        image_url,
        headers={"User-Agent": _UA, "Referer": referer or ""},
    )
    with urllib.request.urlopen(req, timeout=30) as resp, open(path, "wb") as f:
        f.write(resp.read())
    return path


def _is_junk_image(url: str) -> bool:
    """상품 이미지가 아닌 '쓰레기' 이미지(로그인 아이콘, SVG, 공통 static 등)를 걸러냅니다."""
    u = (url or "").lower()
    if not u:
        return True
    if u.endswith(".svg"):
        return True
    if u.startswith("data:"):          # base64 지연로딩 임시 이미지(플레이스홀더)
        return True
    junk_marks = ("/login/", "/nid/", "static/nid", "icon-", "sprite", "blank",
                  "/common/", "placeholder", "loading")
    return any(m in u for m in junk_marks)


def image_from_url(product_url: str) -> dict:
    """
    한국 마켓 상품 URL에서 대표이미지를 찾아 내려받습니다.
    반환: {"seed_type":"url", "seed_ref": url, "seed_image_url": ..., "seed_image_path": ...}
    """
    product_url = product_url.strip()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                user_agent=_UA, viewport={"width": 1280, "height": 1600}
            )
            page.goto(product_url, wait_until="load", timeout=45000)
            # 지연 로딩 이미지 대비 살짝 스크롤
            for _ in range(3):
                page.mouse.wheel(0, 1500)
                time.sleep(0.5)
            # 진짜 이미지가 로드될 시간을 줌(base64 임시이미지 회피)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            page.mouse.wheel(0, -3000)
            time.sleep(0.5)

            # 봇 차단으로 로그인 페이지로 튕겼는지 먼저 확인
            final_url = (page.url or "").lower()
            if any(k in final_url for k in ("nid.naver", "/login", "nidlogin", "captcha")):
                raise RuntimeError(
                    "네이버 쇼핑 카탈로그/검색 페이지는 봇 차단이 있어 자동 추출이 막혔습니다"
                    "(로그인 페이지로 튕김). 상품 이미지를 직접 업로드하거나, 판매자의"
                    " 스마트스토어 '상품 상세' URL을 넣어주세요."
                )

            # og:image → 메인 갤러리 순으로, '쓰레기 이미지'는 걸러가며 후보 수집
            candidates: list[str] = []
            for attr in ("property", "name"):
                loc = page.locator(f'meta[{attr}="og:image"]').first
                if loc.count() > 0:
                    c = loc.get_attribute("content")
                    if c:
                        candidates.append(c)
            for sel in (
                "img#repImage",                      # 스마트스토어 대표이미지
                "[class*='_23RpOU6xpc'] img",        # 스마트스토어 상단 갤러리 계열
                ".prod-image__detail img",           # 쿠팡 계열
                "[class*='thumb'] img",
                "img",
            ):
                imgs = page.locator(sel)
                for i in range(min(imgs.count(), 8)):
                    el = imgs.nth(i)
                    c = el.get_attribute("src") or el.get_attribute("data-src") or el.get_attribute("srcset")
                    if c:
                        candidates.append(c.split()[0])  # srcset 이면 첫 URL만

            # 진짜 상품 이미지(http/https, 쓰레기 아님) 우선 선택
            image_url = next(
                (c for c in candidates
                 if not _is_junk_image(c) and (c.startswith("http") or c.startswith("//"))),
                None,
            )
            if not image_url:
                raise RuntimeError(
                    "네이버가 봇 차단으로 페이지를 덜 보내줘서 상품 이미지를 자동으로 "
                    "못 가져왔습니다. 가장 확실한 방법은 상품 이미지를 직접 '업로드'하는 것입니다."
                )

            local_path = _download(image_url, referer=product_url)
        finally:
            browser.close()

    return {
        "seed_type": "url",
        "seed_ref": product_url,
        "seed_image_url": image_url,
        "seed_image_path": local_path,
    }


def image_from_file(path: str) -> dict:
    """로컬 이미지 파일을 씨앗으로 사용합니다."""
    path = os.path.abspath(path.strip().strip('"'))
    if not os.path.exists(path):
        raise RuntimeError(f"이미지 파일을 찾을 수 없습니다: {path}")
    return {
        "seed_type": "image",
        "seed_ref": os.path.basename(path),
        "seed_image_url": None,
        "seed_image_path": path,
    }
