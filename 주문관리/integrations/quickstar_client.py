# ==========================================================
# 퀵스타(배송대행지) 오픈API 연동 (integrations/quickstar_client.py)
# ----------------------------------------------------------
# 쿠팡 주문의 수취인 정보를 퀵스타 배대지에 자동으로 접수하기 위한 연동입니다.
#
# 명세 출처: 퀵스타가 API 신청자에게 주는 가이드 문서 (docs/quickstar_api.md에 정리)
# 아래 내용은 전부 그 문서에서 확인한 것이고, 추측으로 만든 필드는 없습니다.
#
# 인증(2026-07-23 읽기전용 조회 API로 실제 확인함):
#   - 접수/조회 API : 헤더 "user-session"에 토큰키(.env의 QUICKSTAR_API_KEY)
#   - 목록 조회 API : 헤더 "key"에 가이드 문서에 공개된 조회 전용 키
#
# 주의: submit_application()은 실제로 배대지 신청이 생기는 요청입니다.
# 화면에서 반드시 확인 창을 거친 뒤에만 호출해야 합니다.
# ==========================================================

import requests

import config

BASE_URL = "https://quickstar.co.kr/elpisapi2/"

# 목록 조회(품목/운송방법/운송회사/부가서비스)용 키입니다. 회원별 키가 아니라
# 가이드 문서에 그대로 공개되어 있는 공용 조회 키입니다.
LOOKUP_KEY = "AfTYzVW7K0Pj4mjvZHA63CD9JUfpONdMHxYwj3gERWVJUDX1GJe5hs2dDvwjJdBd3rPXdCXsb"

# 운송방법(ctrNum에 넣는 tr_no) - 가이드 문서 기준
TRANSPORT_METHODS = {
    "34": "항공(일반)",
    "37": "해운(평택)",
    "38": "해운(자가)",
    "42": "해운(인천)",
    "43": "항공(자가)",
}
DEFAULT_TRANSPORT_NO = "34"  # 항공(일반)

TIMEOUT_SECONDS = 20


class QuickstarError(Exception):
    """퀵스타 API 호출이 실패했을 때 발생시킵니다."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable  # True면 잠시 후 다시 시도할 만한 일시 오류(5xx·통신오류)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    return session


def _check_credentials() -> None:
    if not config.QUICKSTAR_API_KEY:
        raise QuickstarError("퀵스타 토큰키(.env의 QUICKSTAR_API_KEY)가 설정되지 않았습니다.")
    if not config.QUICKSTAR_USER_ID:
        raise QuickstarError("퀵스타 아이디(.env의 QUICKSTAR_USER_ID)가 설정되지 않았습니다.")


def _parse(response: requests.Response) -> dict:
    """퀵스타 응답을 확인해서, 실패면 QuickstarError로 바꿔 던집니다."""
    if response.status_code >= 400:
        # 5xx(서버 일시 오류)와 429(요청 과다)는 재시도 가능, 그 외 4xx는 재시도해도 소용없음.
        # ★429를 재시도에 포함: 송장 자동입력처럼 GR 여러 건을 빠르게 연속 조회할 때
        #   순간적으로 레이트리밋에 걸리면 재시도 없이 '오류'로 떨어지던 문제를 막습니다.
        raise QuickstarError(
            f"퀵스타 서버 오류입니다 (상태코드 {response.status_code}).",
            retryable=(response.status_code >= 500 or response.status_code == 429),
        )
    try:
        body = response.json()
    except ValueError:
        raise QuickstarError(f"퀵스타 응답을 해석할 수 없습니다: {response.text[:200]}", retryable=True)

    # 퀵스타는 code "1"이 성공, "2"가 실패입니다 (HTTP 상태코드는 실패해도 200).
    if str(body.get("code")) != "1":
        raise QuickstarError(body.get("message") or "알 수 없는 오류가 발생했습니다.")
    return body


def _get_with_retry(path: str, *, headers: dict, params: dict = None, attempts: int = 3) -> dict:
    """GET 요청을 보내고 _parse까지 하되, 일시 오류(5xx·타임아웃·통신오류)는 몇 번 재시도합니다.
    ★송장 자동입력처럼 여러 건을 연속 조회할 때, 한 건이 일시 오류로 빠지면 그 주문만
    송장이 안 채워지던 문제를 줄입니다. 업무오류(code 2 등)는 재시도해도 소용없어 즉시 올립니다."""
    import time

    last = None
    for attempt in range(attempts):
        try:
            response = _session().get(BASE_URL + path, headers=headers, params=params, timeout=TIMEOUT_SECONDS)
            return _parse(response)
        except QuickstarError as error:
            last = error
            if not getattr(error, "retryable", False) or attempt == attempts - 1:
                raise
        except requests.exceptions.RequestException as error:
            last = QuickstarError(f"퀵스타 통신 오류: {error}", retryable=True)
            if attempt == attempts - 1:
                raise last
        time.sleep(1.0 * (attempt + 1))  # 1s → 2s 백오프
    raise last  # (도달하지 않음)


# ------------------------------------------------------
# 목록 조회 (읽기 전용 - 데이터를 바꾸지 않습니다)
# ------------------------------------------------------

def _lookup(path: str) -> dict:
    response = _session().get(BASE_URL + path, headers={"key": LOOKUP_KEY}, timeout=TIMEOUT_SECONDS)
    return _parse(response)


def fetch_transport_methods() -> list:
    """운송방법 목록을 가져옵니다. [{"tr_no": "34", "tr_name": "항공(일반)"}, ...]"""
    return _lookup("transport_api.php").get("data") or []


def fetch_transport_companies() -> list:
    """운송회사 목록을 가져옵니다."""
    return _lookup("transportCompany_api.php").get("data") or []


def fetch_hscodes() -> list:
    """품목(HS코드) 목록을 가져옵니다. 4천 건이 넘어서 화면에서는 검색해서 씁니다."""
    return _lookup("hscode_api.php").get("data") or []


def fetch_extra_services() -> dict:
    """부가서비스 목록을 가져옵니다. {"order": [...], "ship": [...], "pojang": [...], "etc": [...]}"""
    return _lookup("extra_service_api.php").get("data") or {}


# ------------------------------------------------------
# 신청서 (접수/조회)
# ------------------------------------------------------

def build_receiver_info(order: dict, ship_memo: str = "", compulsion_agree: bool = False) -> dict:
    """
    우리 프로그램의 주문 하나에서 퀵스타 RecInfo(수취인 정보)를 만듭니다.
    전화번호는 안심번호(0502...)가 아니라 통관용 실제 번호를 씁니다.
    """
    shipping = order.get("shipping") or {}
    phone = shipping.get("customs_phone") or shipping.get("receiver_phone_raw") or ""
    receiver = {
        "receiverName": shipping.get("receiver_name") or "",
        "zipCode": shipping.get("zip_code") or "",
        "addr1": shipping.get("address_basic") or "",
        "addr2": shipping.get("address_detail") or "",
        "receiverPhone": phone,
        "personalNum": shipping.get("pccc") or "",
    }
    if ship_memo:
        receiver["shipMemo"] = ship_memo
    if compulsion_agree:
        receiver["compulsionAgree"] = 1
    return receiver


def find_missing_receiver_fields(receiver: dict) -> list:
    """퀵스타가 필수로 요구하는 수취인 항목 중 비어있는 것을 찾아 한글 이름으로 돌려줍니다."""
    required = {
        "receiverName": "수취인명",
        "zipCode": "우편번호",
        "addr1": "주소",
        "receiverPhone": "전화번호",
        "personalNum": "개인통관고유부호",
    }
    return [label for field, label in required.items() if not (receiver.get(field) or "").strip()]


def submit_application(
    receiver: dict,
    transport_no: str = DEFAULT_TRANSPORT_NO,
    auto_payment: bool = False,
    fast_shipping: bool = False,
    insurance: bool = False,
    item_list: list = None,
) -> dict:
    """
    퀵스타에 배송대행 신청서를 접수합니다. **실제로 신청이 생기는 요청입니다.**

    item_list(상품정보)는 퀵스타 문서상 필수 항목이지만, 지금은 수취인 정보만
    자동화하기로 해서 기본적으로 보내지 않습니다. 퀵스타가 상품정보를 요구하며
    거부하면 그 오류 메시지가 그대로 화면에 표시됩니다.

    돌려주는 값: {"order_no": 신청번호, "invoice": 운송장번호, "message": 안내문구}
    """
    _check_credentials()

    payload = {
        "userId": config.QUICKSTAR_USER_ID,
        "ctrNum": transport_no,
        "depositType": "1" if auto_payment else "",
        "fastType": "1" if fast_shipping else "",
        "RecInfo": [receiver],
    }
    if insurance:
        payload["bohumType"] = 1
    if item_list:
        payload["itemList"] = item_list

    response = _session().post(
        BASE_URL + "application_api.php",
        headers={"user-session": config.QUICKSTAR_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=TIMEOUT_SECONDS,
    )
    body = _parse(response)
    return {
        "order_no": body.get("orderNo") or "",
        "invoice": body.get("invoice") or "",
        "message": body.get("message") or "",
    }


def fetch_application(order_no: str) -> dict:
    """
    접수한 신청서를 조회합니다 (읽기 전용).
    order_no는 접수 응답으로 받은 그룹번호(ex: GR2511197392215)입니다.
    """
    _check_credentials()
    return _get_with_retry(
        "applicationInquiry_api.php",
        headers={"user-session": config.QUICKSTAR_API_KEY},
        params={"userId": config.QUICKSTAR_USER_ID, "orderNo": order_no},
    )
