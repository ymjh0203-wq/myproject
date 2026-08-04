"""쿠팡파트너스(어필리에이트) Open API — 상품 검색 클라이언트.

인증 방식은 쿠팡 공식 문서의 CEA(HMAC-SHA256) 서명 방식을 따른다.
문서: https://developers.coupang.com/hc/en-us/articles/360033461914-Creating-HMAC-Signature

주의: 검색 API는 시간당 최대 10회, 키워드당 최대 50개 상품만 반환한다(공식 확인됨).
이 제한은 db.repository의 api_usage_log 테이블로 추적한다.
"""

import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests

from app.config.settings import (
    COUPANG_ACCESS_KEY,
    COUPANG_SECRET_KEY,
    PARTNERS_API_HOST,
    PARTNERS_SEARCH_MAX_LIMIT,
    PARTNERS_SEARCH_PATH,
)


class PartnersApiError(Exception):
    pass


class PartnersApiCredentialsMissing(PartnersApiError):
    pass


class PartnersApiRateLimitExceeded(PartnersApiError):
    pass


def _build_authorization_header(method: str, path: str, query: str, access_key: str, secret_key: str) -> str:
    signed_date = time.strftime("%y%m%d", time.gmtime()) + "T" + time.strftime("%H%M%S", time.gmtime()) + "Z"
    message = signed_date + method + path + query
    signature = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        f"CEA algorithm=HmacSHA256, access-key={access_key}, "
        f"signed-date={signed_date}, signature={signature}"
    )


def search_products(keyword: str, limit: int = 20) -> list[dict]:
    """키워드로 상품을 검색해 원본 응답 항목(dict) 리스트를 반환한다.

    호출 전에 반드시 collector.rate_limit로 시간당 호출 횟수를 확인해야 한다
    (이 함수 자체는 rate limit를 강제하지 않는다).
    """
    if not COUPANG_ACCESS_KEY or not COUPANG_SECRET_KEY:
        raise PartnersApiCredentialsMissing(
            "COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY가 설정되지 않았습니다. "
            "프로젝트 폴더에 .env 파일을 만들고 값을 채워주세요 (.env.example 참고)."
        )
    if not COUPANG_ACCESS_KEY.isascii() or not COUPANG_SECRET_KEY.isascii():
        raise PartnersApiCredentialsMissing(
            ".env의 키 값에 영문/숫자가 아닌 문자가 섞여 있는 것 같습니다. "
            "메모장에서 .env 파일을 다시 열어, '=' 바로 뒤부터 키 값 끝까지만 남기고 "
            "앞뒤 공백이나 이상한 문자가 없는지 확인 후 다시 저장해주세요."
        )

    limit = max(1, min(limit, PARTNERS_SEARCH_MAX_LIMIT))
    query = urlencode({"keyword": keyword, "limit": limit})
    authorization = _build_authorization_header(
        "GET", PARTNERS_SEARCH_PATH, "?" + query, COUPANG_ACCESS_KEY, COUPANG_SECRET_KEY
    )

    response = requests.get(
        PARTNERS_API_HOST + PARTNERS_SEARCH_PATH,
        params={"keyword": keyword, "limit": limit},
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json;charset=UTF-8",
        },
        timeout=10,
    )

    if response.status_code != 200:
        raise PartnersApiError(f"API 요청 실패 (HTTP {response.status_code}): {response.text[:300]}")

    body = response.json()
    data = body.get("data")
    if isinstance(data, dict):
        products = data.get("productData", [])
    elif isinstance(data, list):
        products = data
    else:
        products = []

    if not products and body.get("rCode") not in (None, "0") and body.get("code") not in (None, 0):
        raise PartnersApiError(f"API가 오류를 반환했습니다: {body}")

    return products
