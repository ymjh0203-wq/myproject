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
import json
import os
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SEEDS_DIR = os.path.join(BASE_DIR, "seeds")
# 네이버용 고정 프로필(headful) — 봇 차단 우회용. 쿠키가 쌓여 더 사람처럼 보임.
NAVER_PROFILE_DIR = os.path.join(BASE_DIR, ".naver_profile")

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


def _dump_seed_debug(page, info: dict) -> None:
    """네이버 페이지에서 실제로 뭘 받았는지 seeds/ 에 스크린샷+정보로 남깁니다(원인 파악용)."""
    try:
        _ensure_seeds_dir()
        page.screenshot(path=os.path.join(SEEDS_DIR, "debug_seed.png"))
        with open(os.path.join(SEEDS_DIR, "debug_seed.txt"), "w", encoding="utf-8") as f:
            f.write(json.dumps(info, ensure_ascii=False, indent=2)[:8000])
    except Exception:
        pass


def image_from_url(product_url: str) -> dict:
    """
    한국 마켓 상품 URL에서 대표이미지를 찾아 내려받습니다.
    반환: {"seed_type":"url", "seed_ref": url, "seed_image_url": ..., "seed_image_path": ...}
    """
    product_url = product_url.strip()
    os.makedirs(NAVER_PROFILE_DIR, exist_ok=True)
    with sync_playwright() as p:
        # headful(화면 보이는 실제 브라우저) + 고정 프로필 → 네이버 봇 차단 우회
        context = p.chromium.launch_persistent_context(
            NAVER_PROFILE_DIR,
            headless=False,
            locale="ko-KR",
            user_agent=_UA,
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(product_url, wait_until="load", timeout=45000)

            # 페이지에서 og:image + 실제 이미지 URL 을 뽑는 JS
            JS = """() => {
                const meta = document.querySelector('meta[property="og:image"], meta[name="og:image"]');
                const og = meta ? meta.content : null;
                const imgs = [...document.querySelectorAll('img')].map(i => ({
                    src: i.currentSrc || i.src || i.getAttribute('data-src') || '',
                    w: i.naturalWidth || 0, h: i.naturalHeight || 0
                })).filter(o => o.src.startsWith('http'));
                return {og, imgs, title: document.title, url: location.href};
            }"""

            def _pick(info: dict):
                cands = []
                if info.get("og"):
                    cands.append(info["og"])
                big = sorted(info.get("imgs", []), key=lambda o: o["w"] * o["h"], reverse=True)
                cands += [o["src"] for o in big]
                return next((c for c in cands if not _is_junk_image(c) and c.startswith("http")), None)

            # 네이버 보안확인(캡차)이 뜨면 창을 열어두고, 사용자가 인증을 끝내
            # 진짜 상품 페이지가 뜰 때까지 최대 180초 기다린다(폴링).
            deadline = time.time() + 180
            image_url = None
            info: dict = {}
            while time.time() < deadline:
                try:
                    page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
                # 지연로딩 유도(살짝 스크롤)
                try:
                    page.mouse.wheel(0, 1500)
                    time.sleep(0.4)
                    page.mouse.wheel(0, -1500)
                except Exception:
                    pass
                try:
                    info = page.evaluate(JS)
                except Exception:
                    info = {}
                image_url = _pick(info)
                if image_url:
                    break
                time.sleep(3)  # 아직 없음(캡차 대기/로딩 중) → 잠시 후 재확인

            _dump_seed_debug(page, info or {"reason": "no_info"})
            if not image_url:
                raise RuntimeError(
                    "제한시간(180초) 안에 상품 이미지를 못 가져왔습니다. 네이버 보안확인이 "
                    "떴다면 그 창에서 인증을 끝낸 뒤 다시 시도해 주세요."
                )

            local_path = _download(image_url, referer=product_url)
        finally:
            context.close()

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
