"""벤치마킹 상품을 바탕으로 타오바오 후보를 수집하고 추천 순위를 계산한다."""

from __future__ import annotations

import math
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import Page, sync_playwright

from benchmark_analyzer import _system_chrome_path
from collector.taobao import CaptchaDetected, DEFAULT_PROFILE_DIR, _looks_like_captcha


BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG_DIR = BASE_DIR / "seeds"

# 자동 번역 서비스에 의존하지 않는 기본 사전입니다. 화면에서 중국어 검색어를 수정할 수 있습니다.
CHINESE_KEYWORDS = {
    "관리기": "微耕机", "농기계": "农业机械", "밭갈이": "旋耕机", "경운기": "耕地机",
    "가방": "包包", "여성가방": "女包", "수납": "收纳", "선반": "置物架",
    "조명": "灯具", "의자": "椅子", "테이블": "桌子", "캠핑": "露营",
    "주방": "厨房", "욕실": "浴室", "자동차": "汽车用品", "공구": "工具",
    "반려동물": "宠物用品", "강아지": "狗狗用品", "고양이": "猫咪用品",
}


def suggest_chinese_query(main_keyword: str, sub_keywords: list[str] | None = None) -> str:
    """확정 키워드로 수정 가능한 중국어 검색어 초안을 만든다."""
    values = [main_keyword, *(sub_keywords or [])]
    translated = [CHINESE_KEYWORDS[value] for value in values if value in CHINESE_KEYWORDS]
    return " ".join(dict.fromkeys(translated))


def extract_item_id(url: str) -> str | None:
    """타오바오·티몰의 여러 URL 형태에서 상품 ID를 읽는다."""
    value = (url or "").replace("&amp;", "&")
    try:
        query = parse_qs(urlparse(value).query)
        for key in ("id", "itemId", "item_id"):
            if query.get(key):
                return query[key][0]
    except ValueError:
        pass
    match = re.search(r"(?:item|detail)[/_-]?(\d{8,})", value, re.I)
    return match.group(1) if match else None


def parse_number(text: str | None) -> int | None:
    """'1.2万+', '3천+', '456人付款' 같은 판매량 표기를 정수로 바꾼다."""
    value = (text or "").replace(",", "").strip()
    match = re.search(r"(\d+(?:\.\d+)?)", value)
    if not match:
        return None
    number = float(match.group(1))
    if "万" in value or "만" in value:
        number *= 10_000
    elif "千" in value or "천" in value:
        number *= 1_000
    return int(number)


def parse_price(text: str | None) -> float | None:
    value = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", (text or "").replace(",", ""))
    match = re.search(r"(?:¥|￥)?\s*(\d+(?:\.\d+)?)", value)
    return float(match.group(1)) if match else None


def merge_and_rank(groups: list[tuple[str, list[dict]]], limit: int) -> list[dict]:
    """검색 경로별 후보를 ID로 합치고 판매신호·정보완성도·교차발견으로 정렬한다."""
    merged: dict[str, dict] = {}
    for route, candidates in groups:
        for candidate in candidates:
            item_id = candidate.get("item_id") or extract_item_id(candidate.get("url", ""))
            if not item_id:
                continue
            current = merged.setdefault(item_id, {**candidate, "item_id": item_id, "routes": []})
            if route not in current["routes"]:
                current["routes"].append(route)
            for key in ("title", "image_url", "price_cny", "sales_count", "url"):
                if not current.get(key) and candidate.get(key):
                    current[key] = candidate[key]

    ranked = []
    for candidate in merged.values():
        sales = candidate.get("sales_count") or 0
        score = min(45.0, math.log10(sales + 1) * 11)
        score += 25 if len(candidate["routes"]) > 1 else 12
        score += 10 if candidate.get("image_url") else 0
        score += 5 if candidate.get("title") else 0
        score += 5 if candidate.get("price_cny") is not None else 0
        candidate["score"] = round(min(score, 100), 1)
        ranked.append(candidate)
    ranked.sort(key=lambda item: (-item["score"], -(item.get("sales_count") or 0)))
    for index, candidate in enumerate(ranked[:limit], 1):
        candidate["rank"] = index
    return ranked[:limit]


def _candidate_snapshot(page: Page) -> list[dict]:
    """현재 검색화면의 상품 카드에서 필요한 정보만 읽는다."""
    return page.evaluate(
        r"""() => {
          const result = [];
          const seen = new Set();
          const anchors = [...document.querySelectorAll('a[href]')];
          for (const anchor of anchors) {
            const href = anchor.href || '';
            const match = href.match(/[?&](?:id|itemId|item_id)=(\d{8,})/i)
              || href.match(/(?:item|detail)[/_-]?(\d{8,})/i);
            if (!match || seen.has(match[1])) continue;
            let card = anchor;
            for (let i = 0; i < 6 && card.parentElement; i++, card = card.parentElement) {
              const cardText = (card.innerText || '').trim();
              if (cardText.length >= 20) break;
            }
            const text = (card.innerText || anchor.innerText || '').replace(/\s+/g, ' ').trim();
            const images = [...card.querySelectorAll('img'), ...anchor.querySelectorAll('img')];
            const usableImages = images.filter(img => {
              const src = img.currentSrc || img.src || img.dataset?.src || '';
              return src && !/(atmosphere|tps-|icon|logo)/i.test(src);
            });
            const image = usableImages.sort((a, b) =>
              ((b.naturalWidth || b.width || 0) * (b.naturalHeight || b.height || 0)) -
              ((a.naturalWidth || a.width || 0) * (a.naturalHeight || a.height || 0))
            )[0] || images[0];
            const imageUrl = image?.currentSrc || image?.src || image?.dataset?.src || '';
            const price = text.match(/[¥￥]\s*([0-9]+(?:\.[0-9]+)?)/)
              || text.match(/(?:价格|到手价)\s*([0-9]+(?:\.[0-9]+)?)/);
            const sales = text.match(/([0-9.]+\s*[万千]?)\s*(?:人付款|已售|付款|销量|sold)/i);
            const title = anchor.getAttribute('title') || image?.getAttribute('alt') || text.slice(0, 180);
            seen.add(match[1]);
            result.push({item_id: match[1], url: href, title, image_url: imageUrl,
              price_text: price?.[1] || '', sales_text: sales?.[1] || '', card_text: text.slice(0, 500)});
          }
          return result;
        }"""
    )


def _normalise(candidates: list[dict]) -> list[dict]:
    for item in candidates:
        item["price_cny"] = parse_price(item.pop("price_text", ""))
        item["sales_count"] = parse_number(item.pop("sales_text", ""))
        if str(item.get("image_url", "")).startswith("//"):
            item["image_url"] = "https:" + item["image_url"]
    return candidates


def _wait_for_candidates(page: Page, seconds: int) -> list[dict]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _looks_like_captcha(page):
            time.sleep(2)
            continue
        candidates = _normalise(_candidate_snapshot(page))
        if candidates:
            return candidates
        time.sleep(2)
    if _looks_like_captcha(page):
        raise CaptchaDetected("타오바오 로그인 또는 보안확인이 끝나지 않았습니다.")
    return []


def _download_seed_image(image_url: str) -> Path:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / "benchmark_search_image.jpg"
    request = Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        path.write_bytes(response.read(15_000_000))
    return path


def _try_image_upload(page: Page, image_path: Path) -> bool:
    inputs = page.locator("input[type=file]")
    for index in range(inputs.count()):
        try:
            inputs.nth(index).set_input_files(str(image_path), timeout=3000)
            return True
        except Exception:
            continue
    for selector in ("[class*='camera' i]", "[class*='image-search' i]", "[aria-label*='图片']"):
        try:
            button = page.locator(selector).first
            if not button.count():
                continue
            with page.expect_file_chooser(timeout=3000) as chooser:
                button.click(timeout=3000)
            chooser.value.set_files(str(image_path))
            return True
        except Exception:
            continue
    return False


def search_candidates(image_url: str, chinese_query: str, limit: int = 10,
                      wait_seconds: int = 120, profile_dir: str | None = None) -> dict:
    """한 브라우저 로그인 세션에서 이미지·키워드 검색을 실행한다."""
    if not chinese_query.strip():
        raise ValueError("중국어 검색어를 입력해 주세요.")
    profile = Path(profile_dir or DEFAULT_PROFILE_DIR)
    profile.mkdir(parents=True, exist_ok=True)
    report = {"image": {"ok": False, "count": 0, "message": ""},
              "keyword": {"ok": False, "count": 0, "message": ""}}
    image_candidates: list[dict] = []
    keyword_candidates: list[dict] = []

    with sync_playwright() as playwright:
        launch = dict(user_data_dir=str(profile), headless=False, locale="zh-CN",
                      viewport={"width": 1366, "height": 900},
                      args=["--disable-blink-features=AutomationControlled"])
        chrome = _system_chrome_path()
        if chrome:
            launch["executable_path"] = chrome
        context = playwright.chromium.launch_persistent_context(**launch)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            # 키워드 검색은 이미지 검색 실패 여부와 무관하게 항상 실행합니다.
            try:
                page.goto(f"https://s.taobao.com/search?q={quote(chinese_query.strip())}",
                          wait_until="domcontentloaded", timeout=60_000)
                keyword_candidates = _wait_for_candidates(page, wait_seconds)
                report["keyword"] = {"ok": bool(keyword_candidates),
                                     "count": len(keyword_candidates),
                                     "message": "완료" if keyword_candidates else "상품 카드를 찾지 못했습니다."}
            except Exception as error:
                report["keyword"]["message"] = str(error)

            try:
                seed_image = _download_seed_image(image_url)
                page.goto("https://www.taobao.com", wait_until="domcontentloaded", timeout=60_000)
                if not _try_image_upload(page, seed_image):
                    raise RuntimeError("PC 타오바오 화면에서 이미지검색 버튼을 찾지 못했습니다.")
                image_candidates = _wait_for_candidates(page, wait_seconds)
                report["image"] = {"ok": bool(image_candidates), "count": len(image_candidates),
                                   "message": "완료" if image_candidates else "검색 결과를 찾지 못했습니다."}
            except Exception as error:
                report["image"]["message"] = str(error)

            candidates = merge_and_rank(
                [("키워드", keyword_candidates), ("이미지", image_candidates)], limit
            )
            return {"candidates": candidates, "report": report, "query": chinese_query.strip()}
        finally:
            context.close()
