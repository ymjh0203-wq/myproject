# ==========================================================
# 네이버 스마트스토어(커머스API) 클라이언트 (integrations/naver_client.py)
# ----------------------------------------------------------
# 스마트스토어 주문을 커머스API로 받아오기 위한 클라이언트입니다.
#   - 인증: 애플리케이션 ID(client_id) + 시크릿(secret)으로 OAuth 토큰 발급
#           (서명은 bcrypt.hashpw(f"{client_id}_{timestamp}", secret) → base64)
#           ★이 방식은 상품등록 프로젝트(naver_commerce.py)에서 실제로 검증된 것과 동일합니다.
#   - 키는 쿠팡과 똑같이 market_accounts 테이블에 저장합니다
#     (api_access_key = client_id, api_secret_key = secret).
#
# 주문 조회 흐름(커머스API 표준):
#   1) 변경 상품주문 조회: GET /v1/pay-order/seller/product-orders/last-changed-statuses
#       → 최근 바뀐 productOrderId 목록을 받습니다(폴링).
#   2) 상품주문 상세 조회: POST /v1/pay-order/seller/product-orders/query
#       → 그 id들의 수취인·상품·금액 등 상세를 받습니다.
#
# ★지금은 "연결 테스트 + 조회"까지만 구현합니다. 발송처리(송장등록) 같은 되돌리기 어려운
#   작업은 실제 주문이 들어와 흐름을 검증한 뒤에 붙입니다.
# ==========================================================

from __future__ import annotations

import base64
import re
import time

import requests

TOKEN_URL = "https://api.commerce.naver.com/external/v1/oauth2/token"
BASE_URL = "https://api.commerce.naver.com/external"


class NaverCommerceError(RuntimeError):
    pass


def validate_credentials(client_id: str, secret: str) -> tuple[str, str]:
    client_id = (client_id or "").strip()
    secret = (secret or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,55}", client_id):
        raise NaverCommerceError("애플리케이션 ID(client_id)를 빠짐없이 입력해 주세요.")
    # 시크릿은 bcrypt 해시 형식($2a/$2b/$2y$...). 재발급한 값을 처음부터 끝까지 넣어야 합니다.
    if not re.fullmatch(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{22,}", secret):
        raise NaverCommerceError(
            "시크릿 형식이 맞지 않습니다. 커머스API센터에서 발급/재발급한 시크릿을 "
            "처음($2...)부터 끝까지 그대로 복사해 주세요."
        )
    return client_id, secret


def _request_token(client_id: str, secret: str) -> dict:
    import bcrypt

    client_id, secret = validate_credentials(client_id, secret)
    timestamp = int(time.time() * 1000)
    try:
        signature = base64.b64encode(
            bcrypt.hashpw(f"{client_id}_{timestamp}".encode(), secret.encode())
        ).decode()
    except ValueError:
        raise NaverCommerceError("시크릿 형식을 확인해 주세요.") from None

    try:
        response = requests.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "timestamp": timestamp,
                "client_secret_sign": signature,
                "grant_type": "client_credentials",
                "type": "SELF",
            },
            timeout=(10, 25),
            allow_redirects=False,
        )
    except requests.RequestException:
        raise NaverCommerceError(
            "네이버에 연결하지 못했습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요."
        ) from None

    if response.status_code != 200:
        if response.status_code == 403:
            msg = "접근이 거부됐습니다. 커머스API센터의 'API 호출 IP' 설정과 애플리케이션 활성 상태를 확인해 주세요."
        elif response.status_code in (400, 401):
            msg = "인증 실패입니다. 애플리케이션 ID·새 시크릿, 등록한 공인 IP, 컴퓨터 날짜·시간을 확인해 주세요."
        elif response.status_code == 429:
            msg = "요청이 많습니다. 잠시 후 다시 시도해 주세요."
        else:
            msg = f"네이버 토큰 발급 실패({response.status_code})."
        raise NaverCommerceError(msg)

    try:
        payload = response.json()
        if not payload.get("access_token"):
            raise ValueError
        return payload
    except (ValueError, TypeError, AttributeError):
        raise NaverCommerceError("네이버 인증 응답을 확인하지 못했습니다. 다시 시도해 주세요.") from None


def check_connection(client_id: str, secret: str) -> dict:
    """토큰만 발급받아 연결을 확인합니다(주문을 바꾸는 요청은 하지 않음)."""
    payload = _request_token(client_id, secret)
    return {"ok": True, "expires_in": int(payload.get("expires_in", 10800))}


class NaverCommerceClient:
    """스마트스토어(커머스API) 주문 조회용 클라이언트."""

    def __init__(self, client_id: str, secret: str, account_name: str = None):
        self.client_id = client_id
        self.account_name = account_name or client_id
        self._token = _request_token(client_id, secret)["access_token"]

    def request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = requests.request(
                method,
                BASE_URL + path,
                headers={"Authorization": "Bearer " + self._token},
                timeout=(10, 90),
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException:
            raise NaverCommerceError("네이버 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.") from None
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not 200 <= response.status_code < 300:
            message = str(payload.get("message", "요청을 처리하지 못했습니다."))[:500]
            raise NaverCommerceError(f"네이버 오류({response.status_code}): {message}")
        return payload

    # ---- 주문 조회 (커머스API 표준 2단계) ----
    def fetch_changed_product_orders(self, last_changed_from: str, last_changed_to: str = None,
                                     last_changed_type: str = None) -> list:
        """
        변경된 상품주문의 productOrderId 목록을 받습니다(폴링용).
        last_changed_from/to: ISO8601(예: 2026-11-11T00:00:00.000+09:00)
        last_changed_type: 없으면 전체. (PAYED/DISPATCHED/... 등 특정 변경만 받고 싶을 때)
        반환: [{productOrderId, productOrderStatus, ...}, ...]
        """
        params = {"lastChangedFrom": last_changed_from}
        if last_changed_to:
            params["lastChangedTo"] = last_changed_to
        if last_changed_type:
            params["lastChangedType"] = last_changed_type
        payload = self.request(
            "GET", "/v1/pay-order/seller/product-orders/last-changed-statuses", params=params
        )
        data = payload.get("data") or {}
        return data.get("lastChangeStatuses") or []

    def fetch_product_order_details(self, product_order_ids: list) -> list:
        """상품주문 id 목록의 상세(수취인·상품·금액 등)를 받습니다. (최대 300건/요청)"""
        results = []
        ids = [str(x) for x in product_order_ids if x]
        for start in range(0, len(ids), 300):
            batch = ids[start:start + 300]
            payload = self.request(
                "POST",
                "/v1/pay-order/seller/product-orders/query",
                json={"productOrderIds": batch},
            )
            results.extend(payload.get("data") or [])
        return results
