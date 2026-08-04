# ==========================================================
# 쿠팡 오픈API 연동 (integrations/coupang_client.py)
# ----------------------------------------------------------
# 이 파일은 쿠팡에서 주문 정보를 가져오는 역할을 합니다.
#
# 지금은 "실제 쿠팡 API 문서"를 아직 확보하지 못했기 때문에,
# 실제 연동(real 모드)은 TODO로만 남겨두고, 대신 가짜 주문 데이터를
# 돌려주는 Mock 모드만 동작하게 만들었습니다.
#
# 실제 연동을 구현할 때는 _fetch_real_orders() 함수 안에,
# 공식 문서에서 확인한 정확한 요청/응답 형식으로 채워 넣어야 합니다.
# (추측으로 필드명을 만들어 넣지 않습니다)
#
# Mock/실제 두 모드 모두, 아래와 같은 "우리 프로그램 내부용 공통 형식"으로
# 주문 데이터를 돌려줍니다. (이건 쿠팡의 실제 응답 형식이 아니라,
# 이 프로그램 안에서 쓰기로 정한 형식입니다)
#
#   {
#       "market_order_id": str,      # 쿠팡 주문번호
#       "shipment_box_id": str,      # 배송번호(묶음배송 단위)
#       "ordered_at": str,           # 주문일시
#       "market_status": str,        # 쿠팡 원본 상태
#       "items": [
#           {"market_item_id": str, "product_name": str, "option_name": str,
#            "quantity": int, "sales_amount": int,
#            "seller_product_code": str, "seller_option_code": str,
#            "delivery_charge_type_name": str, "estimated_shipping_date": str},
#           ...
#       ],
#       "shipping_fee": int,
#       "settlement_amount": int or None,   # 확인 안 되면 None (지어내지 않음)
#       "receiver_name": str,
#       "receiver_phone_raw": str,
#       "customs_phone": str (선택),        # 마켓이 통관용 전화번호를 이미 알려준 경우만
#       "pccc": str,
#       "zip_code": str,
#       "address_basic": str,
#       "address_detail": str,
#       "orderer_name": str,          # 구매자 이름 (수령자와 다를 수 있음)
#       "orderer_phone": str,         # 구매자 전화번호
#       "paid_at": str,               # 결제일시
#       "remote_area": bool,          # 도서산간 여부
#       "market_delivery_company_name": str,  # 쿠팡이 알려주는 택배사명 (참고용)
#       "market_invoice_number": str,         # 쿠팡이 알려주는 송장번호 (참고용)
#   }
# ==========================================================

import copy
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

import config

# ----------------------------------------------------------
# 실제 쿠팡 오픈API 정보
# 출처: https://developers.coupang.com (2026-07-18 확인)
#   - 신규주문 조회: "PO list query (paging by day)"
#   - 인증 방식: "Creating HMAC Signature"
# 문서를 AI가 요약해서 가져온 내용이라, 실제 호출 결과와 다르면
# 이 부분(엔드포인트/필드명)을 다시 확인해서 고쳐야 합니다.
# ----------------------------------------------------------
API_BASE_URL = "https://api-gateway.coupang.com"
ORDER_LIST_PATH_TEMPLATE = "/v2/providers/openapi/apis/api/v5/vendors/{vendor_id}/ordersheets"
# 쿠팡 주문 조회는 조회 기간이 32일 미만이어야 합니다("range should less than 32 day").
# 그래서 넓은 기간은 이 값(31일)씩 잘라서 여러 번 조회합니다.
ORDER_QUERY_MAX_DAYS = 31
# 상품준비중 처리(신규주문 -> 발송대기): "Changing the status to Product in Preparation" 문서 기준
ACKNOWLEDGEMENT_PATH_TEMPLATE = "/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/ordersheets/acknowledgement"
ACKNOWLEDGEMENT_BATCH_SIZE = 50  # 한 번에 최대 50건까지 요청 가능
# 송장업로드 처리(발송대기 -> 배송중): "송장업로드 처리" 문서 기준
INVOICE_PATH_TEMPLATE = "/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/orders/invoices"

# 취소/반품 조회: "Return/Cancellation Request List Query" 문서 기준 (2026-07-20 확인)
# 같은 API를 cancelType 파라미터(CANCEL/RETURN)로 구분해서 씁니다.
RETURN_REQUESTS_PATH_TEMPLATE = "/v2/providers/openapi/apis/api/v6/vendors/{vendor_id}/returnRequests"
# 교환 조회: "Exchange APIs" 문서 기준. 엔드포인트/메소드는 확인했지만, 응답의
# 세부 필드 구조는 문서에서 완전히 확인 못 해서 실제 호출 결과를 보고 조정이
# 필요할 수 있습니다 (아래 _convert_real_exchange 참고).
EXCHANGE_REQUESTS_PATH_TEMPLATE = "/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/exchangeRequests"
# 상품문의 조회: "Customer Inquiry Query by Product" 문서 기준. inquiryStartAt~EndAt은
# 최대 7일 범위까지만 허용된다고 문서에 나와있어서, 요청 기간이 길면 7일 단위로 나눠 호출합니다.
PRODUCT_INQUIRIES_PATH_TEMPLATE = "/v2/providers/openapi/apis/api/v5/vendors/{vendor_id}/onlineInquiries"
PRODUCT_INQUIRY_MAX_DAYS = 7
# 상품문의 답변 등록: "Answer to Customer Product Inquiry" 문서 기준 (2026-07-24 확인)
#   POST .../v4/vendors/{vendorId}/onlineInquiries/{inquiryId}/replies
#   본문: {"content": 답변내용, "vendorId": 판매자ID, "replyBy": 답변자 WING 아이디}
#   - 줄바꿈은 \n 으로 넣습니다.
#   - 같은 문의에 두 번 답변하면 오류가 납니다.
#   - 조회 API가 v5인 것과 달리 답변 등록은 v4입니다. (문서 기준)
PRODUCT_INQUIRY_REPLY_PATH_TEMPLATE = (
    "/v2/providers/openapi/apis/api/v4/vendors/{vendor_id}/onlineInquiries/{inquiry_id}/replies"
)
# 콜센터문의 조회: "Query of Coupang Contact Center Inquiries" 문서 기준.
# partnerCounselingStatus가 필수 파라미터인데, 정확한 허용값 목록을 문서에서
# 완전히 확인하지 못했습니다. "ALL"로 시도하고, 쿠팡이 거부하면 오류 메시지에
# 실제 허용값이 나올 가능성이 높아서 그걸 보고 고칠 예정입니다.
CALL_CENTER_INQUIRIES_PATH_TEMPLATE = "/v2/providers/openapi/apis/api/v5/vendors/{vendor_id}/callCenterInquiries"
# 상품조회: 판매자상품코드(sellerProductId)로 상품 상세를 가져옵니다. 응답에 상품
# 페이지 링크를 만드는 데 필요한 productId(노출상품ID)와 옵션별 itemId가 들어있습니다.
SELLER_PRODUCT_PATH_TEMPLATE = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{seller_product_id}"


def _order_within_period(ordered_at: str, period_from, period_to) -> bool:
    """주문일시(ordered_at)가 [period_from, period_to] 날짜 범위 안에 있는지 확인합니다."""
    order_date = datetime.fromisoformat(ordered_at).date()
    if period_from and order_date < period_from:
        return False
    if period_to and order_date > period_to:
        return False
    return True


class CoupangApiError(Exception):
    """쿠팡 API 호출 중 문제가 생겼을 때 발생시키는 에러입니다."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable  # True면 나중에 다시 시도해볼 만한 오류, False면 재시도해도 소용없는 오류


def _build_mock_orders() -> list:
    """
    테스트용 가짜 주문 6건을 만들어 돌려줍니다.
    이름/전화번호/주소/통관고유부호는 전부 가짜이며 실제 인물과 무관합니다.

    각 단계(신규주문/발송대기/배송중/배송완료)를 골고루 테스트할 수 있도록
    market_status를 실제 쿠팡 상태값으로 나눠뒀습니다:
      MOCK0001, MOCK0002 -> ACCEPT(신규주문)
      MOCK0003            -> INSTRUCT(발송대기)
      MOCK0004            -> DEPARTURE(배송중)
      MOCK0005            -> DELIVERING(배송중)
      MOCK0006            -> FINAL_DELIVERY(배송완료)

    일부러 형식이 틀린 항목(이름/전화번호/통관고유부호/우편번호)도 하나씩
    섞어뒀습니다. 통관정보 형식검사를 테스트할 때 씁니다.
    """
    now = datetime.now()

    return [
        {
            "market_order_id": "MOCK0001",
            "shipment_box_id": "MOCKBOX0001",
            "ordered_at": (now - timedelta(minutes=10)).isoformat(timespec="seconds"),
            "market_status": "ACCEPT",
            "items": [
                {"market_item_id": "ITEM0001", "product_name": "가상 상품 A", "option_name": "블랙 / M",
                 "quantity": 1, "sales_amount": 15000,
                 "seller_product_code": "SP0001", "seller_option_code": "SO0001",
                 "delivery_charge_type_name": "무료배송", "estimated_shipping_date": (now + timedelta(days=2)).date().isoformat()},
            ],
            "shipping_fee": 0,
            "settlement_amount": 13200,
            "receiver_name": "테스트고객1",
            "receiver_phone_raw": "010-0000-0001",
            "pccc": "P123456789012",
            "zip_code": "06134",
            "address_basic": "서울특별시 강남구 테스트로 1",
            "address_detail": "101동 101호",
            "orderer_name": "테스트고객1",
            "orderer_phone": "010-0000-0001",
            "paid_at": (now - timedelta(minutes=10)).isoformat(timespec="seconds"),
            "remote_area": False,
            "market_delivery_company_name": "",
            "market_invoice_number": "",
        },
        {
            "market_order_id": "MOCK0002",
            "shipment_box_id": "MOCKBOX0002",
            "ordered_at": (now - timedelta(minutes=25)).isoformat(timespec="seconds"),
            "market_status": "ACCEPT",
            "items": [
                {"market_item_id": "ITEM0002", "product_name": "가상 상품 B", "option_name": "화이트 / L",
                 "quantity": 2, "sales_amount": 24000,
                 "seller_product_code": "SP0002", "seller_option_code": "SO0002",
                 "delivery_charge_type_name": "무료배송", "estimated_shipping_date": (now + timedelta(days=2)).date().isoformat()},
            ],
            "shipping_fee": 0,
            "settlement_amount": 21120,
            "receiver_name": "테스트고객2",
            "receiver_phone_raw": "010-0000-0002",
            "pccc": "P223456789012",
            "zip_code": "48058",
            "address_basic": "부산광역시 해운대구 테스트로 2",
            "address_detail": "202동 202호",
            # 선물 주문(구매자와 수령자가 다른 경우) 테스트용
            "orderer_name": "테스트구매자2",
            "orderer_phone": "010-9000-0002",
            "paid_at": (now - timedelta(minutes=25)).isoformat(timespec="seconds"),
            "remote_area": False,
            "market_delivery_company_name": "",
            "market_invoice_number": "",
        },
        {
            # 통관고유부호가 비어있는 경우 ('형식오류' 테스트용)
            "market_order_id": "MOCK0003",
            "shipment_box_id": "MOCKBOX0003",
            "ordered_at": (now - timedelta(minutes=40)).isoformat(timespec="seconds"),
            "market_status": "INSTRUCT",
            "items": [
                {"market_item_id": "ITEM0003", "product_name": "가상 상품 C", "option_name": "-",
                 "quantity": 1, "sales_amount": 9900,
                 "seller_product_code": "SP0003", "seller_option_code": "SO0003",
                 "delivery_charge_type_name": "유료배송", "estimated_shipping_date": (now + timedelta(days=3)).date().isoformat()},
            ],
            "shipping_fee": 3000,
            "settlement_amount": 8712,
            "receiver_name": "테스트고객3",
            "receiver_phone_raw": "010-0000-0003",
            "pccc": "",
            "zip_code": "35240",
            "address_basic": "대전광역시 유성구 테스트로 3",
            "address_detail": "303호",
            "orderer_name": "테스트고객3",
            "orderer_phone": "010-0000-0003",
            "paid_at": (now - timedelta(minutes=40)).isoformat(timespec="seconds"),
            "remote_area": False,
            "market_delivery_company_name": "",
            "market_invoice_number": "",
        },
        {
            # 전화번호가 비어있는 경우 ('형식오류' 테스트용)
            "market_order_id": "MOCK0004",
            "shipment_box_id": "MOCKBOX0004",
            "ordered_at": (now - timedelta(minutes=55)).isoformat(timespec="seconds"),
            "market_status": "DEPARTURE",
            "items": [
                {"market_item_id": "ITEM0004", "product_name": "가상 상품 D", "option_name": "One Size",
                 "quantity": 3, "sales_amount": 45000,
                 "seller_product_code": "SP0004", "seller_option_code": "SO0004",
                 "delivery_charge_type_name": "무료배송", "estimated_shipping_date": (now - timedelta(days=1)).date().isoformat()},
            ],
            "shipping_fee": 0,
            "settlement_amount": 39600,
            "receiver_name": "테스트고객4",
            "receiver_phone_raw": "",
            "pccc": "P423456789012",
            "zip_code": "16487",
            "address_basic": "경기도 수원시 테스트로 4",
            "address_detail": "404동 404호",
            "orderer_name": "테스트고객4",
            "orderer_phone": "",
            "paid_at": (now - timedelta(minutes=55)).isoformat(timespec="seconds"),
            "remote_area": False,
            # 쿠팡 Wing에서 직접 송장을 등록한 경우를 흉내냅니다 (우리 앱은 아직 모름)
            "market_delivery_company_name": "CJ대한통운",
            "market_invoice_number": "MOCKINV0004",
        },
        {
            # 우편번호 자릿수가 틀린 경우 (3-4 단계 '형식오류' 테스트용)
            "market_order_id": "MOCK0005",
            "shipment_box_id": "MOCKBOX0005",
            "ordered_at": (now - timedelta(minutes=70)).isoformat(timespec="seconds"),
            "market_status": "DELIVERING",
            "items": [
                {"market_item_id": "ITEM0005", "product_name": "가상 상품 E", "option_name": "-",
                 "quantity": 1, "sales_amount": 12000,
                 "seller_product_code": "SP0005", "seller_option_code": "SO0005",
                 "delivery_charge_type_name": "도서산간추가배송비", "estimated_shipping_date": (now - timedelta(days=2)).date().isoformat()},
            ],
            "shipping_fee": 0,
            "settlement_amount": 10560,
            "receiver_name": "테스트고객5",
            "receiver_phone_raw": "010-0000-0005",
            "pccc": "P523456789012",
            "zip_code": "1234",
            "address_basic": "인천광역시 남동구 테스트로 5",
            "address_detail": "505호",
            "orderer_name": "테스트고객5",
            "orderer_phone": "010-0000-0005",
            "paid_at": (now - timedelta(minutes=70)).isoformat(timespec="seconds"),
            # 도서산간 여부 테스트용
            "remote_area": True,
            "market_delivery_company_name": "",
            "market_invoice_number": "",
        },
        {
            # 수취인 이름이 너무 짧은 경우 ('형식오류' 테스트용)
            "market_order_id": "MOCK0006",
            "shipment_box_id": "MOCKBOX0006",
            "ordered_at": (now - timedelta(minutes=85)).isoformat(timespec="seconds"),
            "market_status": "FINAL_DELIVERY",
            "items": [
                {"market_item_id": "ITEM0006", "product_name": "가상 상품 F", "option_name": "-",
                 "quantity": 1, "sales_amount": 8000,
                 "seller_product_code": "SP0006", "seller_option_code": "SO0006",
                 "delivery_charge_type_name": "무료배송", "estimated_shipping_date": (now - timedelta(days=5)).date().isoformat()},
            ],
            "shipping_fee": 0,
            "settlement_amount": 7040,
            "receiver_name": "김",
            "receiver_phone_raw": "010-0000-0006",
            "pccc": "P623456789012",
            "zip_code": "63122",
            "address_basic": "제주특별자치도 제주시 테스트로 6",
            "address_detail": "606호",
            "orderer_name": "김",
            "orderer_phone": "010-0000-0006",
            "paid_at": (now - timedelta(minutes=85)).isoformat(timespec="seconds"),
            "remote_area": True,
            "market_delivery_company_name": "",
            "market_invoice_number": "",
        },
    ]


class CoupangClient:
    """
    쿠팡 주문 정보를 가져오는 클라이언트입니다. Mock 모드와 실제 모드를 지원합니다.

    vendor_id/access_key/secret_key를 안 주면 .env(config.COUPANG_*)의 기본 계정을
    씁니다. 여러 상점(마켓 계정)을 쓰는 경우, market_repository에서 계정 정보를
    가져와 여기에 직접 넣어주면 그 계정으로 호출합니다.
    """

    def __init__(self, vendor_id: str = None, access_key: str = None, secret_key: str = None, account_name: str = None):
        self.mode = "mock" if config.is_mock_mode() else "real"
        self.vendor_id = vendor_id or config.COUPANG_VENDOR_ID
        self.access_key = access_key or config.COUPANG_ACCESS_KEY
        self.secret_key = secret_key or config.COUPANG_SECRET_KEY
        self.account_name = account_name  # 표시용 (예: '굿디얼') - API 호출에는 안 씀

    def fetch_orders_by_status(self, market_status: str, period_from=None, period_to=None) -> list:
        """
        지정한 쿠팡 원본 상태(market_status)의 주문을 지정한 결제일시 기간에서 가져옵니다.
        period_from/period_to는 date 객체입니다. 안 주면(None) 기간 제한 없이 가져옵니다.
        Mock 모드에서는 가짜 주문 중 이 상태/기간에 해당하는 것만 걸러서 돌려줍니다.
        """
        if self.mode == "mock":
            orders = copy.deepcopy(_build_mock_orders())
            orders = [o for o in orders if o["market_status"] == market_status]
            return [o for o in orders if _order_within_period(o["ordered_at"], period_from, period_to)]
        return self._fetch_real_orders(market_status, period_from, period_to)

    def fetch_new_orders(self, period_from=None, period_to=None) -> list:
        """지정한 결제일시 기간의 신규(결제완료/ACCEPT) 주문을 가져옵니다."""
        return self.fetch_orders_by_status("ACCEPT", period_from, period_to)

    def fetch_order_detail(self, market_order_id: str) -> dict | None:
        """
        주문 하나의 최신 상세정보를 다시 가져옵니다.
        (신규주문을 발송대기로 넘기기 전에, 최신 정보로 한 번 더 확인하는 용도)
        Mock 모드에서는 같은 가짜 데이터를 "새로 조회한 것"처럼 돌려줍니다.
        """
        if self.mode == "mock":
            for order in _build_mock_orders():
                if order["market_order_id"] == market_order_id:
                    return copy.deepcopy(order)
            return None
        return self._fetch_real_order_detail(market_order_id)

    def acknowledge_orders(self, shipment_box_ids: list) -> list:
        """
        쿠팡에 "상품준비중" 처리를 요청합니다. (우리 프로그램의 신규주문 -> 발송대기
        이동과 짝을 이루는 실제 쿠팡 쪽 상태 변경입니다)

        shipment_box_ids: 문자열 배송번호 목록
        돌려주는 값: [{"shipment_box_id": str, "succeeded": bool, "message": str}, ...]
        (shipment_box_ids와 같은 순서/개수로 결과가 옵니다)

        Mock 모드에서는 실제 호출 없이 전부 성공한 것으로 흉내냅니다.
        """
        if not shipment_box_ids:
            return []

        if self.mode == "mock":
            return [
                {"shipment_box_id": sid, "succeeded": True, "message": "(Mock) 처리되었습니다."}
                for sid in shipment_box_ids
            ]
        return self._acknowledge_real_orders(shipment_box_ids)

    def register_invoice(
        self,
        order: dict,
        delivery_company_code: str,
        invoice_number: str,
        estimated_shipping_date: str,
    ) -> dict:
        """
        쿠팡에 송장(택배사+운송장번호)을 등록합니다. 이 주문의 상품 항목마다
        하나씩 등록 정보를 만들어서 한 번에 보냅니다 (같은 택배 상자로 보내는
        상품들은 같은 송장번호를 공유합니다).

        order: order_repository.list_orders_by_work_status()가 돌려주는 형태
               (order["items"]에 market_item_id가 들어있어야 합니다)
        돌려주는 값: {"succeeded": bool, "message": str}

        Mock 모드에서는 실제 호출 없이 성공한 것으로 흉내냅니다.
        """
        if self.mode == "mock":
            return {"succeeded": True, "message": "(Mock) 송장이 등록되었습니다."}
        return self._register_invoice_real(order, delivery_company_code, invoice_number, estimated_shipping_date)

    def fetch_claims(self, claim_type: str, period_from=None, period_to=None) -> list:
        """
        취소요청(claim_type='CANCEL') 또는 반품요청(claim_type='RETURN')을 가져옵니다.
        쿠팡은 같은 API를 cancelType 파라미터로 구분해서 씁니다.
        """
        if self.mode == "mock":
            claims = copy.deepcopy(_build_mock_claims())
            claims = [c for c in claims if c["claim_type"] == claim_type]
            return [c for c in claims if _order_within_period(c["requested_at"], period_from, period_to)]
        return self._fetch_real_claims(claim_type, period_from, period_to)

    def fetch_exchange_requests(self, period_from=None, period_to=None) -> list:
        """교환요청을 가져옵니다."""
        if self.mode == "mock":
            exchanges = copy.deepcopy(_build_mock_exchange_requests())
            return [e for e in exchanges if _order_within_period(e["requested_at"], period_from, period_to)]
        return self._fetch_real_exchange_requests(period_from, period_to)

    def fetch_product_inquiries(self, period_from=None, period_to=None) -> list:
        """상품문의(구매자가 상품 페이지에서 남긴 질문)를 가져옵니다."""
        if self.mode == "mock":
            inquiries = copy.deepcopy(_build_mock_product_inquiries())
            return [i for i in inquiries if _order_within_period(i["inquiry_at"], period_from, period_to)]
        return self._fetch_real_product_inquiries(period_from, period_to)

    def fetch_call_center_inquiries(self, period_from=None, period_to=None) -> list:
        """콜센터문의(고객센터로 들어온 문의)를 가져옵니다."""
        if self.mode == "mock":
            inquiries = copy.deepcopy(_build_mock_call_center_inquiries())
            return [i for i in inquiries if _order_within_period(i["inquiry_at"], period_from, period_to)]
        return self._fetch_real_call_center_inquiries(period_from, period_to)

    def fetch_seller_product(self, seller_product_id: str) -> dict:
        """
        판매자상품코드(sellerProductId)로 상품 정보를 조회해서, 상품 페이지 링크를
        만드는 데 필요한 productId(노출상품ID)와 옵션별 itemId를 뽑아옵니다.

        돌려주는 형식:
          {"product_id": "9016547324",
           "items": [{"vendor_item_id": "95331332943", "item_id": "26434243739"}, ...]}

        조회 실패(존재하지 않거나 쿠팡 일시 오류 등)하면 None을 돌려줍니다.
        """
        if self.mode == "mock":
            return None
        if not seller_product_id:
            return None
        self._check_credentials()
        path = SELLER_PRODUCT_PATH_TEMPLATE.format(seller_product_id=seller_product_id)
        try:
            body = self._request("GET", path, {})
        except CoupangApiError:
            # 특정 상품이 조회 안 되는 경우(TEMP_FAILURE 등)가 있어, 링크 하나 못
            # 만든다고 화면 전체가 멈추면 안 되므로 조용히 None을 돌려줍니다.
            return None
        data = body.get("data") or {}
        product_id = data.get("productId")
        if not product_id:
            return None
        items = []
        for item in data.get("items") or []:
            vendor_item_id = item.get("vendorItemId")
            if vendor_item_id:
                items.append(
                    {"vendor_item_id": str(vendor_item_id), "item_id": str(item.get("itemId") or "")}
                )
        return {"product_id": str(product_id), "items": items}

    # ------------------------------------------------------
    # 아래는 실제 쿠팡 API 연동 부분입니다.
    # (2026-07-18) 공식 문서(developers.coupang.com)에서 확인한 내용으로
    # 구현했습니다. 처음 실제 호출해볼 때 응답이 문서와 다르면 이 부분을
    # 다시 확인해야 할 수 있습니다.
    # ------------------------------------------------------

    def _build_auth_headers(self, method: str, path: str, query_string: str) -> dict:
        """
        쿠팡 오픈API 인증 헤더(HMAC-SHA256 서명)를 만듭니다.

        서명 문자열 = "서명시각 + HTTP메소드 + API경로 + 쿼리스트링" 을 이어붙인 것이고,
        이걸 Secret Key로 HMAC-SHA256 서명한 값을 Authorization 헤더에 넣습니다.
        (출처: 쿠팡 개발자센터 "Creating HMAC Signature" 문서)
        """
        signed_date = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
        message = f"{signed_date}{method}{path}{query_string}"
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        authorization = (
            f"CEA algorithm=HmacSHA256, access-key={self.access_key}, "
            f"signed-date={signed_date}, signature={signature}"
        )
        return {
            "Authorization": authorization,
            "Content-Type": "application/json;charset=UTF-8",
        }

    def _check_credentials(self) -> None:
        if not (self.vendor_id and self.access_key and self.secret_key):
            account_desc = f" ('{self.account_name}' 계정)" if self.account_name else ""
            raise CoupangApiError(
                f"쿠팡 API 인증정보{account_desc}가 설정되지 않았습니다.",
                retryable=False,
            )

    def _request(self, method: str, path: str, params: dict, json_body: dict = None) -> dict:
        """실제 쿠팡 API에 요청 하나를 보내고 응답 JSON을 돌려줍니다."""
        # 서명에 쓴 쿼리스트링과 실제로 전송되는 쿼리스트링이 글자 하나까지 똑같아야
        # 서명이 일치합니다. requests의 params=를 쓰면 인코딩 방식이 서명 계산과
        # 달라질 수 있어서, urlencode로 직접 만든 문자열을 URL에 그대로 붙여 보냅니다.
        # (PATCH/POST처럼 본문(JSON body)이 있는 요청은 쿼리스트링이 없으므로 서명에는
        # 영향 없습니다 - 공식 문서 예제에도 본문은 서명 대상에 포함되지 않습니다)
        query_string = urlencode(params)
        headers = self._build_auth_headers(method, path, query_string)
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        url = f"{API_BASE_URL}{path}"
        if query_string:
            url = f"{url}?{query_string}"

        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=15,
            )
        except requests.exceptions.RequestException as error:
            raise CoupangApiError(f"쿠팡 API 요청 중 통신 오류가 발생했습니다: {error}", retryable=True)

        if response.status_code == 401 or response.status_code == 403:
            raise CoupangApiError(
                f"쿠팡 API 인증에 실패했습니다 (상태코드 {response.status_code}): {response.text[:300]}",
                retryable=False,
            )
        if response.status_code == 429:
            raise CoupangApiError("쿠팡 API 호출 한도를 초과했습니다. 잠시 후 다시 시도해주세요.", retryable=True)
        if response.status_code >= 500:
            raise CoupangApiError(f"쿠팡 서버 오류입니다 (상태코드 {response.status_code}).", retryable=True)
        if response.status_code >= 400:
            raise CoupangApiError(
                f"쿠팡 API 요청이 거부되었습니다 (상태코드 {response.status_code}): {response.text[:300]}",
                retryable=False,
            )

        body = response.json()
        # 일부 API(예: callCenterInquiries)는 HTTP 상태코드는 200인데 응답 본문
        # 안의 "code" 필드에 오류코드(500 등)를 담아 보냅니다. 이것도 놓치지 않게
        # 따로 확인합니다.
        body_code = body.get("code") if isinstance(body, dict) else None
        if isinstance(body_code, int) and body_code >= 400:
            raise CoupangApiError(
                f"쿠팡 API가 오류를 응답했습니다 (코드 {body_code}): {body.get('message') or ''}",
                retryable=body_code >= 500,
            )

        return body

    @staticmethod
    def _money_to_won(value) -> int:
        """
        쿠팡 API의 금액 표현을 원화 정수로 바꿔줍니다.
        {"units": 15000, "nanos": 0} 같은 객체이거나, 그냥 숫자(15000)로 올 수도 있어서
        둘 다 처리합니다.
        """
        if value is None:
            return 0
        if isinstance(value, dict):
            units = value.get("units") or 0
            nanos = value.get("nanos") or 0
            return int(units) + round(nanos / 1_000_000_000)
        return int(value)

    def _convert_real_order(self, raw: dict) -> dict:
        """쿠팡 API가 준 주문 하나(raw dict)를 우리 프로그램 내부 형식으로 바꿉니다."""
        receiver = raw.get("receiver") or {}
        oversea_info = raw.get("overseaShippingInfoDto") or {}
        orderer = raw.get("orderer") or {}

        items = []
        for item in raw.get("orderItems") or []:
            items.append(
                {
                    "market_item_id": str(item.get("vendorItemId") or ""),
                    "product_name": item.get("sellerProductName") or item.get("vendorItemName") or "",
                    "option_name": item.get("sellerProductItemName") or "",
                    "quantity": item.get("shippingCount") or 0,
                    "sales_amount": self._money_to_won(item.get("orderPrice")),
                    "seller_product_code": str(item.get("sellerProductId") or ""),
                    "seller_option_code": item.get("externalVendorSkuCode") or "",
                    "delivery_charge_type_name": item.get("deliveryChargeTypeName") or "",
                    "estimated_shipping_date": item.get("estimatedShippingDate") or "",
                }
            )

        return {
            "market_order_id": str(raw.get("orderId") or ""),
            "shipment_box_id": str(raw.get("shipmentBoxId") or ""),
            "ordered_at": raw.get("orderedAt") or "",
            "market_status": raw.get("status") or "",
            "items": items,
            "shipping_fee": self._money_to_won(raw.get("shippingPrice")),
            # TODO: 이 API 응답에서 "정산금(정산예상금액)" 필드를 찾지 못했습니다.
            # 정산 정보는 쿠팡의 별도 API에서 제공할 가능성이 있어서, 확인 전까지는
            # 지어내지 않고 비워둡니다.
            "settlement_amount": None,
            "receiver_name": receiver.get("name") or "",
            "receiver_phone_raw": receiver.get("safeNumber") or receiver.get("receiverNumber") or "",
            # 쿠팡이 해외구매대행 주문에는 통관용 실제 전화번호를 별도로 알려줍니다.
            "customs_phone": oversea_info.get("ordererPhoneNumber") or None,
            "pccc": oversea_info.get("personalCustomsClearanceCode") or "",
            "zip_code": receiver.get("postCode") or "",
            "address_basic": receiver.get("addr1") or "",
            "address_detail": receiver.get("addr2") or "",
            # 구매자(주문한 사람)는 수령자와 다를 수 있어서 쿠팡이 따로 알려줍니다.
            "orderer_name": orderer.get("name") or "",
            "orderer_phone": orderer.get("safeNumber") or orderer.get("ordererNumber") or "",
            "paid_at": raw.get("paidAt") or "",
            "remote_area": bool(raw.get("remoteArea")),
            "market_delivery_company_name": raw.get("deliveryCompanyName") or "",
            "market_invoice_number": raw.get("invoiceNumber") or "",
        }

    def _fetch_real_orders(self, market_status: str, period_from, period_to) -> list:
        """실제 쿠팡 오픈API로 지정한 상태(market_status)의 주문을 조회합니다."""
        self._check_credentials()

        date_from = period_from or (datetime.now() - timedelta(days=1)).date()
        date_to = period_to or datetime.now().date()

        path = ORDER_LIST_PATH_TEMPLATE.format(vendor_id=self.vendor_id)

        all_orders = []
        # 쿠팡 주문 조회는 "조회 기간이 32일 미만이어야 한다"는 제한이 있습니다
        # (초과하면 400 "range should less than 32 day"). 기간이 넓으면 수집이 통째로
        # 실패해서, 넘어간 주문이 예전 단계에 그대로 남습니다. 그래서 31일씩 잘라서
        # 여러 번 조회한 뒤 합칩니다. (취소/반품 조회가 7일씩 나누는 것과 같은 방식)
        chunk_start = date_from
        while chunk_start <= date_to:
            chunk_end = min(chunk_start + timedelta(days=ORDER_QUERY_MAX_DAYS - 1), date_to)
            next_token = ""
            while True:
                params = {
                    "createdAtFrom": f"{chunk_start.strftime('%Y-%m-%d')}+09:00",
                    "createdAtTo": f"{chunk_end.strftime('%Y-%m-%d')}+09:00",
                    "status": market_status,
                    "maxPerPage": 50,
                }
                if next_token:
                    params["nextToken"] = next_token

                body = self._request("GET", path, params)
                raw_orders = body.get("data") or []
                all_orders.extend(self._convert_real_order(raw) for raw in raw_orders)

                next_token = body.get("nextToken") or ""
                if not next_token or not raw_orders:
                    break

            chunk_start = chunk_end + timedelta(days=1)

        return all_orders

    def _fetch_real_order_detail(self, market_order_id: str) -> dict:
        """TODO: 주문 하나만 다시 조회하는 API(Single PO query)는 아직 연결하지 않았습니다."""
        raise NotImplementedError(
            "주문 상세 재조회는 아직 구현되지 않았습니다. (Single PO query API 연동 예정)"
        )

    def _acknowledge_real_orders(self, shipment_box_ids: list) -> list:
        """
        실제 쿠팡 오픈API로 "상품준비중" 처리를 요청합니다.
        출처: 쿠팡 개발자센터 "Changing the status to Product in Preparation" 문서
        - PATCH /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/ordersheets/acknowledgement
        - 본문: {"vendorId": ..., "shipmentBoxIds": [...]}  (최대 50개씩)
        - 응답의 data.responseList 안에 항목별 succeed(true/false) 여부가 담겨옵니다.
        """
        self._check_credentials()
        path = ACKNOWLEDGEMENT_PATH_TEMPLATE.format(vendor_id=self.vendor_id)

        results = []
        for start in range(0, len(shipment_box_ids), ACKNOWLEDGEMENT_BATCH_SIZE):
            batch = shipment_box_ids[start:start + ACKNOWLEDGEMENT_BATCH_SIZE]
            body = {
                "vendorId": self.vendor_id,
                "shipmentBoxIds": [int(sid) for sid in batch],
            }
            response = self._request("PATCH", path, params={}, json_body=body)
            response_list = ((response.get("data") or {}).get("responseList")) or []

            # 응답에 결과가 하나도 없으면(예상 못한 형식), 이 배치 전체를 실패로 처리합니다.
            if not response_list:
                results.extend(
                    {"shipment_box_id": sid, "succeeded": False, "message": "쿠팡 응답에서 처리 결과를 확인할 수 없습니다."}
                    for sid in batch
                )
                continue

            for item in response_list:
                results.append(
                    {
                        "shipment_box_id": str(item.get("shipmentBoxId")),
                        "succeeded": bool(item.get("succeed")),
                        "message": item.get("resultMessage") or "",
                    }
                )

        return results

    def _register_invoice_real(
        self,
        order: dict,
        delivery_company_code: str,
        invoice_number: str,
        estimated_shipping_date: str,
    ) -> dict:
        """
        실제 쿠팡 오픈API로 송장을 등록합니다.
        출처: 쿠팡 개발자센터 "송장업로드 처리" 문서
        - POST /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/orders/invoices
        - 본문: {"vendorId": ..., "orderSheetInvoiceApplyDtos": [ {shipmentBoxId, orderId,
          deliveryCompanyCode, invoiceNumber, vendorItemId, splitShipping, preSplitShipped,
          estimatedShippingDate}, ... ]}  (주문에 상품이 여러 개면 항목마다 하나씩)
        """
        self._check_credentials()
        path = INVOICE_PATH_TEMPLATE.format(vendor_id=self.vendor_id)

        apply_dtos = [
            {
                "shipmentBoxId": int(order["shipment_box_id"]),
                "orderId": int(order["market_order_id"]),
                "deliveryCompanyCode": delivery_company_code,
                "invoiceNumber": invoice_number,
                "vendorItemId": int(item["market_item_id"]),
                "splitShipping": False,
                "preSplitShipped": False,
                "estimatedShippingDate": estimated_shipping_date,
            }
            for item in order["items"]
        ]

        body = {
            "vendorId": self.vendor_id,
            "orderSheetInvoiceApplyDtos": apply_dtos,
        }

        response = self._request("POST", path, params={}, json_body=body)
        data = response.get("data") or {}
        response_list = data.get("responseList") or []

        # 항목 하나라도 실패하면 전체를 실패로 보고합니다 (부분 성공은 헷갈리므로
        # 다시 시도하도록 유도합니다).
        failed_messages = [
            item.get("resultMessage") for item in response_list if not item.get("succeed") and item.get("resultMessage")
        ]
        if failed_messages or not response_list:
            message = " / ".join(failed_messages) if failed_messages else (data.get("responseMessage") or "처리 결과를 확인할 수 없습니다.")
            return {"succeeded": False, "message": message}

        return {"succeeded": True, "message": "송장이 등록되었습니다."}

    def _convert_real_claim(self, raw: dict, claim_type: str) -> dict:
        """쿠팡 취소/반품 응답 하나를 내부 형식으로 바꿉니다."""
        return {
            "claim_type": claim_type,
            "receipt_id": str(raw.get("receiptId") or ""),
            "market_order_id": str(raw.get("orderId") or ""),
            "receipt_status": raw.get("receiptStatus") or "",
            "reason_category1": raw.get("cancelReasonCategory1") or "",
            "reason_category2": raw.get("cancelReasonCategory2") or "",
            "reason_detail": raw.get("cancelReason") or "",
            "requested_at": raw.get("createdAt") or "",
            "complete_confirm_type": raw.get("completeConfirmType") or "",
            "complete_confirm_date": raw.get("completeConfirmDate") or "",
        }

    def _fetch_real_claims(self, claim_type: str, period_from, period_to) -> list:
        """
        실제 쿠팡 오픈API로 취소/반품 요청을 조회합니다.
        출처: 쿠팡 개발자센터 "Return/Cancellation Request List Query" 문서 (2026-07-20 확인)
        - GET /v2/providers/openapi/apis/api/v6/vendors/{vendorId}/returnRequests
        - cancelType=CANCEL(취소) 또는 RETURN(반품, 기본값)
        """
        self._check_credentials()
        date_from = (period_from or (datetime.now() - timedelta(days=1)).date()).strftime("%Y-%m-%d")
        date_to = (period_to or datetime.now().date()).strftime("%Y-%m-%d")
        path = RETURN_REQUESTS_PATH_TEMPLATE.format(vendor_id=self.vendor_id)

        all_claims = []
        next_token = ""
        while True:
            params = {
                # 실제 호출 결과 확인(2026-07-21): searchType=timeFrame을 지정해야
                # 기간(주문번호 없이) 검색이 되고, 이때는 yyyy-MM-ddTHH:mm 형식이 필요함
                # (searchType 없이 보내면 "OrderId can't be null" 오류가 남)
                "searchType": "timeFrame",
                "createdAtFrom": f"{date_from}T00:00",
                "createdAtTo": f"{date_to}T23:59",
                "cancelType": claim_type,
                "maxPerPage": 50,
            }
            if next_token:
                params["nextToken"] = next_token

            body = self._request("GET", path, params)
            raw_items = body.get("data") or []
            all_claims.extend(self._convert_real_claim(raw, claim_type) for raw in raw_items)

            next_token = body.get("nextToken") or ""
            if not next_token or not raw_items:
                break

        return all_claims

    def _convert_real_exchange(self, raw: dict) -> dict:
        """
        쿠팡 교환요청 응답 하나를 내부 형식으로 바꿉니다.
        주의: 응답의 정확한 필드 구조를 문서에서 완전히 확인하지 못했습니다.
        실제 호출 결과를 보고 필드명을 다시 확인/조정해야 할 수 있습니다.
        """
        return {
            "exchange_id": str(raw.get("exchangeId") or raw.get("id") or ""),
            "market_order_id": str(raw.get("orderId") or ""),
            "status": raw.get("status") or raw.get("exchangeStatus") or "",
            "requested_at": raw.get("createdAt") or raw.get("requestedAt") or "",
        }

    def _fetch_real_exchange_requests(self, period_from, period_to) -> list:
        """
        실제 쿠팡 오픈API로 교환요청을 조회합니다.
        출처: 쿠팡 개발자센터 "Exchange APIs" 문서, 실제 호출로 세부사항 확인(2026-07-21)
        - GET /v2/providers/openapi/apis/api/v4/vendors/{vendorId}/exchangeRequests
        - createdAtFrom/createdAtTo는 초(second)까지 포함한 yyyy-MM-ddTHH:mm:ss 형식
        - 기간은 최대 7일까지만 허용되어서(오류: "createdAtTo - createdAtFrom should less
          then 7day"), 요청 기간이 길면 7일 단위로 나눠서 호출합니다.
        - 응답의 data는 (onlineInquiries와 달리) 바로 배열입니다.
        """
        self._check_credentials()
        date_from = period_from or (datetime.now() - timedelta(days=1)).date()
        date_to = period_to or datetime.now().date()
        path = EXCHANGE_REQUESTS_PATH_TEMPLATE.format(vendor_id=self.vendor_id)

        all_exchanges = []
        chunk_start = date_from
        while chunk_start <= date_to:
            chunk_end = min(chunk_start + timedelta(days=6), date_to)

            next_token = ""
            while True:
                params = {
                    "createdAtFrom": f"{chunk_start.strftime('%Y-%m-%d')}T00:00:00",
                    "createdAtTo": f"{chunk_end.strftime('%Y-%m-%d')}T23:59:59",
                    "maxPerPage": 50,
                }
                if next_token:
                    params["nextToken"] = next_token

                body = self._request("GET", path, params)
                raw_items = body.get("data") or []
                all_exchanges.extend(self._convert_real_exchange(raw) for raw in raw_items)

                next_token = body.get("nextToken") or ""
                if not next_token or not raw_items:
                    break

            chunk_start = chunk_end + timedelta(days=1)

        return all_exchanges

    def _convert_real_product_inquiry(self, raw: dict) -> dict:
        """
        쿠팡 상품문의 응답 하나를 내부 형식으로 바꿉니다.

        쿠팡 응답에는 상품명이 없고 ID(productId/sellerProductId/vendorItemId)만
        들어있습니다. 그래서 ID들을 그대로 저장해두고, 화면에 보여줄 때 우리
        주문 데이터에서 그 ID로 상품명/옵션을 찾아옵니다.
        commentDtoList에는 이미 등록된 답변이 들어있습니다(있으면 답변완료).
        """
        comments = raw.get("commentDtoList") or []
        last_comment = comments[-1] if comments else {}
        order_ids = [str(o) for o in (raw.get("orderIds") or []) if o]
        return {
            "inquiry_id": str(raw.get("inquiryId") or ""),
            "market_item_id": str(raw.get("productId") or ""),
            "seller_product_id": str(raw.get("sellerProductId") or ""),
            "vendor_item_id": str(raw.get("vendorItemId") or ""),
            "order_ids": ",".join(order_ids),
            "content": raw.get("content") or "",
            "inquiry_at": raw.get("inquiryAt") or "",
            "answered": bool(comments),
            "answer_content": last_comment.get("content") or "",
            "answered_at": last_comment.get("inquiryCommentAt") or "",
        }

    def answer_product_inquiry(self, inquiry_id: str, content: str, reply_by: str) -> dict:
        """
        상품문의에 답변을 등록합니다. 실제로 쿠팡 상품페이지에 공개 답변이 올라갑니다.

        reply_by는 답변자의 WING 아이디입니다(쿠팡이 필수로 요구합니다).
        같은 문의에 두 번 답변하면 쿠팡이 오류를 돌려줍니다.
        """
        if not (content or "").strip():
            raise CoupangApiError("답변 내용이 비어 있습니다.", retryable=False)
        if not (reply_by or "").strip():
            raise CoupangApiError(
                "답변자 WING 아이디가 없습니다. 설정 > 마켓 연동 관리에서 상점의 WING 아이디를 입력해주세요.",
                retryable=False,
            )

        if self.mode == "mock":
            return {"succeeded": True, "message": "(Mock) 답변이 등록되었습니다."}

        self._check_credentials()
        path = PRODUCT_INQUIRY_REPLY_PATH_TEMPLATE.format(
            vendor_id=self.vendor_id, inquiry_id=inquiry_id
        )
        body = self._request(
            "POST",
            path,
            {},
            json_body={
                "content": content,
                "vendorId": self.vendor_id,
                "replyBy": reply_by,
            },
        )
        return {"succeeded": True, "message": body.get("message") or "답변이 등록되었습니다."}

    def _fetch_real_product_inquiries(self, period_from, period_to) -> list:
        """
        실제 쿠팡 오픈API로 상품문의를 조회합니다.
        출처: 쿠팡 개발자센터 "Customer Inquiry Query by Product" 문서, 실제 호출로
        세부사항 확인(2026-07-21)
        - GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/onlineInquiries
        - inquiryStartAt~inquiryEndAt은 최대 7일 범위까지만 허용되어서, 요청 기간이
          길면 7일 단위로 나눠서 여러 번 호출합니다.
        - 응답 구조가 다른 API와 다릅니다: data가 바로 배열이 아니라
          {"content": [...], "pagination": {"currentPage", "totalPages", ...}}
          형태의 객체입니다.
        """
        self._check_credentials()
        date_from = period_from or (datetime.now() - timedelta(days=1)).date()
        date_to = period_to or datetime.now().date()
        path = PRODUCT_INQUIRIES_PATH_TEMPLATE.format(vendor_id=self.vendor_id)

        all_inquiries = []
        chunk_start = date_from
        while chunk_start <= date_to:
            chunk_end = min(chunk_start + timedelta(days=PRODUCT_INQUIRY_MAX_DAYS - 1), date_to)

            page_num = 1
            while True:
                params = {
                    "answeredType": "ALL",
                    "inquiryStartAt": chunk_start.strftime("%Y-%m-%d"),
                    "inquiryEndAt": chunk_end.strftime("%Y-%m-%d"),
                    "pageNum": page_num,
                    "pageSize": 50,
                }
                body = self._request("GET", path, params)
                data = body.get("data") or {}
                raw_items = data.get("content") or []
                all_inquiries.extend(self._convert_real_product_inquiry(raw) for raw in raw_items)

                pagination = data.get("pagination") or {}
                current_page = pagination.get("currentPage") or page_num
                total_pages = pagination.get("totalPages") or 1
                if current_page >= total_pages or not raw_items:
                    break
                page_num += 1

            chunk_start = chunk_end + timedelta(days=1)

        return all_inquiries

    def _convert_real_call_center_inquiry(self, raw: dict) -> dict:
        """쿠팡 콜센터문의 응답 하나를 내부 형식으로 바꿉니다."""
        return {
            "inquiry_id": str(raw.get("inquiryId") or ""),
            "market_order_id": str(raw.get("orderId") or ""),
            "inquiry_status": raw.get("inquiryStatus") or "",
            "partner_counseling_status": raw.get("csPartnerCounselingStatus") or "",
            "content": raw.get("content") or "",
            "buyer_phone": raw.get("buyerPhone") or "",
            "inquiry_at": raw.get("inquiryAt") or "",
        }

    def _fetch_real_call_center_inquiries(self, period_from, period_to) -> list:
        """
        실제 쿠팡 오픈API로 콜센터문의를 조회합니다.
        출처: 쿠팡 개발자센터 "Query of Coupang Contact Center Inquiries" 문서 (2026-07-20 확인)
        - GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/callCenterInquiries
        - partnerCounselingStatus가 필수 파라미터인데, 정확한 허용값 목록을 문서에서
          완전히 확인하지 못했습니다. "ALL"로 우선 시도합니다 - 쿠팡이 거부하면
          CoupangApiError 메시지에 실제 허용값이 나올 가능성이 높습니다.
        """
        self._check_credentials()
        date_from = (period_from or (datetime.now() - timedelta(days=1)).date()).strftime("%Y-%m-%d")
        date_to = (period_to or datetime.now().date()).strftime("%Y-%m-%d")
        path = CALL_CENTER_INQUIRIES_PATH_TEMPLATE.format(vendor_id=self.vendor_id)

        all_inquiries = []
        page_num = 1
        while True:
            params = {
                "partnerCounselingStatus": "ALL",
                "inquiryStartAt": date_from,
                "inquiryEndAt": date_to,
                "pageNum": page_num,
                "pageSize": 30,
            }
            body = self._request("GET", path, params)
            raw_items = body.get("data") or []
            all_inquiries.extend(self._convert_real_call_center_inquiry(raw) for raw in raw_items)

            if len(raw_items) < 30:
                break
            page_num += 1

        return all_inquiries


def _build_mock_claims() -> list:
    """테스트용 가짜 취소/반품 3건을 만들어 돌려줍니다."""
    now = datetime.now()
    return [
        {
            "claim_type": "CANCEL",
            "receipt_id": "MOCKCANCEL0001",
            "market_order_id": "MOCK0001",
            "receipt_status": "CC",
            "reason_category1": "단순변심",
            "reason_category2": "-",
            "reason_detail": "다른 상품을 잘못 주문했습니다.",
            "requested_at": (now - timedelta(hours=2)).isoformat(timespec="seconds"),
            "complete_confirm_type": "",
            "complete_confirm_date": "",
        },
        {
            "claim_type": "RETURN",
            "receipt_id": "MOCKRETURN0001",
            "market_order_id": "MOCK0005",
            "receipt_status": "RETURNS_UNCHECKED",
            "reason_category1": "상품불량",
            "reason_category2": "파손",
            "reason_detail": "배송 중 파손된 채로 도착했습니다.",
            "requested_at": (now - timedelta(hours=5)).isoformat(timespec="seconds"),
            "complete_confirm_type": "",
            "complete_confirm_date": "",
        },
        {
            "claim_type": "RETURN",
            "receipt_id": "MOCKRETURN0002",
            "market_order_id": "MOCK0006",
            "receipt_status": "RETURNS_COMPLETED",
            "reason_category1": "단순변심",
            "reason_category2": "-",
            "reason_detail": "생각했던 것과 달라서 반품합니다.",
            "requested_at": (now - timedelta(days=1)).isoformat(timespec="seconds"),
            "complete_confirm_type": "AUTO",
            "complete_confirm_date": now.isoformat(timespec="seconds"),
        },
    ]


def _build_mock_exchange_requests() -> list:
    """테스트용 가짜 교환요청 2건을 만들어 돌려줍니다."""
    now = datetime.now()
    return [
        {
            "exchange_id": "MOCKEXCHANGE0001",
            "market_order_id": "MOCK0002",
            "status": "RECEIPT",
            "requested_at": (now - timedelta(hours=3)).isoformat(timespec="seconds"),
        },
        {
            "exchange_id": "MOCKEXCHANGE0002",
            "market_order_id": "MOCK0003",
            "status": "PROGRESS",
            "requested_at": (now - timedelta(days=2)).isoformat(timespec="seconds"),
        },
    ]


def _build_mock_product_inquiries() -> list:
    """테스트용 가짜 상품문의 2건을 만들어 돌려줍니다."""
    now = datetime.now()
    return [
        {
            "inquiry_id": "MOCKINQ0001",
            "market_item_id": "ITEM0001",
            "seller_product_id": "SP0001",
            "vendor_item_id": "VI0001",
            "order_ids": "",
            "content": "이 상품 해외배송인가요? 배송 얼마나 걸리나요?",
            "inquiry_at": (now - timedelta(hours=1)).isoformat(timespec="seconds"),
            "answered": False,
            "answer_content": "",
            "answered_at": "",
        },
        {
            "inquiry_id": "MOCKINQ0002",
            "market_item_id": "ITEM0003",
            "seller_product_id": "SP0003",
            "vendor_item_id": "VI0003",
            "order_ids": "",
            "content": "사이즈 문의드립니다.",
            "inquiry_at": (now - timedelta(days=1)).isoformat(timespec="seconds"),
            "answered": True,
            "answer_content": "정사이즈로 나왔습니다.",
            "answered_at": (now - timedelta(hours=20)).isoformat(timespec="seconds"),
        },
    ]


def _build_mock_call_center_inquiries() -> list:
    """테스트용 가짜 콜센터문의 2건을 만들어 돌려줍니다."""
    now = datetime.now()
    return [
        {
            "inquiry_id": "MOCKCC0001",
            "market_order_id": "MOCK0004",
            "inquiry_status": "RECEIPT",
            "partner_counseling_status": "REQUEST",
            "content": "주문한 상품이 언제 오나요?",
            "buyer_phone": "010-0000-0004",
            "inquiry_at": (now - timedelta(hours=4)).isoformat(timespec="seconds"),
        },
        {
            "inquiry_id": "MOCKCC0002",
            "market_order_id": "MOCK0006",
            "inquiry_status": "COMPLETE",
            "partner_counseling_status": "COMPLETE",
            "content": "반품 절차가 궁금합니다.",
            "buyer_phone": "010-0000-0006",
            "inquiry_at": (now - timedelta(days=1, hours=2)).isoformat(timespec="seconds"),
        },
    ]
