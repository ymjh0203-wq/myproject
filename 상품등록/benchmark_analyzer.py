"""국내 벤치마킹 상품 URL 분석 서비스.

보이는 Chrome 창을 유지하면서 사용자가 보안확인을 처리할 수 있게 하고,
상품명·대표이미지·가격·판매점·리뷰 신호를 추출한다.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright


BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / ".benchmark_profile"
DEBUG_DIR = BASE_DIR / "seeds"

ALLOWED_HOST_SUFFIXES = (
    "smartstore.naver.com",
    "brand.naver.com",
    "search.shopping.naver.com",
    "coupang.com",
)

CHALLENGE_WORDS = (
    "보안 확인",
    "자동입력 방지",
    "자동입력방지",
    "captcha",
    "로봇이 아닙니다",
)


class BenchmarkAnalysisError(RuntimeError):
    """기준상품 분석이 완료되지 않았을 때 발생한다."""


def validate_product_url(url: str) -> str:
    """지원하는 국내 상품 URL인지 검사하고 정리된 URL을 반환한다."""
    value = url.strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("http 또는 https로 시작하는 상품 URL을 입력해 주세요.")
    if not any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_HOST_SUFFIXES):
        raise ValueError("현재는 스마트스토어·네이버 브랜드스토어·네이버쇼핑·쿠팡 URL을 지원합니다.")
    return value


def detect_marketplace(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "coupang.com" in host:
        return "coupang"
    if "search.shopping.naver.com" in host:
        return "naver_shopping"
    if "brand.naver.com" in host:
        return "brandstore"
    return "smartstore"


def _system_chrome_path() -> str | None:
    """설치된 실제 Chrome을 우선 사용해 자동 브라우저 차단 가능성을 낮춘다."""
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    return next((str(path) for path in candidates if path.is_file()), None)


def _extract_snapshot(page: Page) -> dict:
    """현재 화면에서 상품 후보 정보를 읽는다. 외부 입력을 실행하지 않고 DOM만 조회한다."""
    return page.evaluate(
        r"""() => {
          const text = (document.body?.innerText || '').replace(/\u00a0/g, ' ');
          const meta = (property) =>
            document.querySelector(`meta[property="${property}"], meta[name="${property}"]`)
              ?.getAttribute('content')?.trim() || '';

          const products = [];
          for (const node of document.querySelectorAll('script[type="application/ld+json"]')) {
            try {
              const value = JSON.parse(node.textContent || '{}');
              const queue = Array.isArray(value) ? value : [value];
              for (const item of queue) {
                if (item && (item['@type'] === 'Product' || item.name || item.image)) products.push(item);
              }
            } catch (_) {}
          }
          const product = products[0] || {};
          const offers = Array.isArray(product.offers) ? product.offers[0] : (product.offers || {});

          const selectorText = (selectors) => {
            for (const selector of selectors) {
              const element = document.querySelector(selector);
              const value = element?.textContent?.trim();
              if (value) return value;
            }
            return '';
          };

          const imageCandidates = [];
          const addImage = (src, width=0, height=0, source='dom') => {
            if (!src || !/^https?:\/\//i.test(src)) return;
            imageCandidates.push({src, width:Number(width)||0, height:Number(height)||0, source});
          };
          const productImage = Array.isArray(product.image) ? product.image[0] : product.image;
          addImage(typeof productImage === 'object' ? productImage?.url : productImage, 2000, 2000, 'jsonld');
          addImage(meta('og:image'), 1800, 1800, 'og');
          for (const image of document.images) {
            const src = image.currentSrc || image.src || image.dataset?.src || '';
            const rect = image.getBoundingClientRect();
            addImage(src, image.naturalWidth || rect.width, image.naturalHeight || rect.height, 'img');
          }

          const reviewPatterns = [
            /(?:리뷰|상품평|구매평)\s*([0-9,.]+)/,
            /([0-9,.]+)\s*(?:건|개)?\s*(?:리뷰|상품평|구매평)/
          ];
          let reviewText = '';
          for (const pattern of reviewPatterns) {
            const found = text.match(pattern);
            if (found) { reviewText = found[1]; break; }
          }
          const labeledPrice = text.match(/상품\s*가격\s*([0-9,]+)\s*원/)
            || text.match(/판매가\s*([0-9,]+)\s*원/);
          const visiblePrice = selectorText([
            '[class*="discounted"]', '[class*="sale_price"]',
            '[class*="total-price"]', '[class*="prod-price"]',
            '[class*="price"] strong'
          ]);

          return {
            url: location.href,
            pageTitle: document.title || '',
            bodyText: text.slice(0, 12000),
            title: selectorText([
              'h1', '[class*="product_title"]', '[class*="ProductName"]',
              '[class*="prod-buy-header"] h2', '[class*="title"] h2'
            ]) || product.name || meta('og:title'),
            priceText: (labeledPrice?.[1] || visiblePrice || String(offers.price || offers.lowPrice || '')),
            shopName: product.brand?.name || product.seller?.name || meta('og:site_name') || '',
            reviewText,
            imageCandidates
          };
        }"""
    )


def _clean_title(value: str) -> str:
    title = re.sub(r"\s+", " ", value or "").strip()
    title = re.sub(r"\s*[-|:]\s*(네이버 스마트스토어|스마트스토어|쿠팡).*$", "", title)
    # 스마트스토어 문서 제목은 보통 "상품명 : 판매점명" 형태다.
    title = re.sub(r"\s+:\s+[^:]{1,60}$", "", title)
    return title[:500]


def _parse_number(value: str) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return None
    match = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)", raw)
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    if "만" in raw:
        number *= 10_000
    return int(number)


def _derive_shop_name(snapshot: dict) -> str:
    explicit = (snapshot.get("shopName") or "").strip()
    if explicit:
        return explicit
    page_title = (snapshot.get("pageTitle") or "").strip()
    match = re.search(r"\s+:\s+([^:]{1,60})$", page_title)
    return match.group(1).strip() if match else ""


def _choose_image(candidates: list[dict]) -> str | None:
    """아이콘·로고를 제외하고 상품일 가능성이 높은 큰 이미지를 선택한다."""
    junk = ("sprite", "icon", "logo", "favicon", "placeholder", "loading", "/common/")
    usable = []
    for item in candidates:
        src = str(item.get("src") or "")
        lowered = src.lower()
        if not src.startswith(("http://", "https://")) or any(mark in lowered for mark in junk):
            continue
        area = int(item.get("width") or 0) * int(item.get("height") or 0)
        source_bonus = 10_000_000 if item.get("source") in {"jsonld", "og"} else 0
        usable.append((source_bonus + area, src))
    return max(usable, default=(0, None))[1]


def _looks_like_challenge(snapshot: dict) -> bool:
    haystack = f"{snapshot.get('pageTitle', '')}\n{snapshot.get('bodyText', '')}".lower()
    return any(word.lower() in haystack for word in CHALLENGE_WORDS)


def _is_usable(snapshot: dict) -> bool:
    title = _clean_title(snapshot.get("title") or "")
    image = _choose_image(snapshot.get("imageCandidates") or [])
    return bool(title and image and not _looks_like_challenge(snapshot))


def _write_debug(page: Page, snapshot: dict, tag: str) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(DEBUG_DIR / f"benchmark_{tag}.png"), full_page=False)
    except Exception:
        pass
    with (DEBUG_DIR / f"benchmark_{tag}.json").open("w", encoding="utf-8") as file:
        json.dump(snapshot, file, ensure_ascii=False, indent=2)


def analyze_benchmark_product(url: str, wait_seconds: int = 300) -> dict:
    """국내 상품 URL을 분석한다. 보안확인은 사용자가 열린 창에서 직접 처리한다."""
    product_url = validate_product_url(url)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        launch_args = {
            "user_data_dir": str(PROFILE_DIR),
            "headless": False,
            "locale": "ko-KR",
            "viewport": {"width": 1366, "height": 900},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        chrome = _system_chrome_path()
        if chrome:
            launch_args["executable_path"] = chrome

        context = playwright.chromium.launch_persistent_context(**launch_args)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(product_url, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.monotonic() + max(30, min(wait_seconds, 600))
            snapshot: dict = {}

            while time.monotonic() < deadline:
                try:
                    snapshot = _extract_snapshot(page)
                    if _is_usable(snapshot):
                        # 가격·리뷰처럼 늦게 갱신되는 값이 안정될 시간을 준 뒤 최종 추출한다.
                        time.sleep(1.5)
                        snapshot = _extract_snapshot(page)
                        break
                except Exception:
                    snapshot = {"url": page.url, "pageTitle": page.title(), "bodyText": ""}
                time.sleep(2)
            else:
                _write_debug(page, snapshot, "timeout")
                if _looks_like_challenge(snapshot):
                    raise BenchmarkAnalysisError(
                        "보안확인이 완료되지 않았습니다. 열린 창에서 인증을 마친 뒤 다시 분석해 주세요."
                    )
                raise BenchmarkAnalysisError(
                    "상품명과 대표이미지를 확인하지 못했습니다. 진단파일을 저장했습니다."
                )

            # 성공 직전 최종 화면과 추출값을 남겨 실제 테스트 결과를 재현할 수 있게 한다.
            _write_debug(page, snapshot, "success")
            result = {
                "source_url": product_url,
                "resolved_url": snapshot.get("url") or product_url,
                "marketplace": detect_marketplace(product_url),
                "title": _clean_title(snapshot.get("title") or snapshot.get("pageTitle") or ""),
                "image_url": _choose_image(snapshot.get("imageCandidates") or []),
                "price_krw": _parse_number(snapshot.get("priceText") or ""),
                "price_text": snapshot.get("priceText") or "",
                "shop_name": _derive_shop_name(snapshot),
                "review_count": _parse_number(snapshot.get("reviewText") or ""),
                "analyzed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            return result
        finally:
            context.close()
