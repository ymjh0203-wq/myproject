# ==========================================================
# 취소/반품/교환/문의 수집 업무 로직 (services/claims_sync_service.py)
# ----------------------------------------------------------
# sync_service.py(주문)와 같은 패턴이지만, 다른 데이터 종류(취소/반품/교환/
# 상품문의/콜센터문의)를 다룹니다. 등록된 모든 쿠팡 계정을 순서대로 돌면서
# 가져오고, 이미 있는 건 갱신, 처음 보는 건 새로 저장합니다.
# ==========================================================

import json
from datetime import datetime

from integrations.coupang_client import CoupangApiError, CoupangClient
from repositories import claims_repository, exchange_repository, inquiry_repository, market_repository, settings_repository

MARKET_NAME = "coupang"


def _client_for_account(account: dict) -> CoupangClient:
    return CoupangClient(
        vendor_id=account["api_vendor_id"],
        access_key=account["api_access_key"],
        secret_key=account["api_secret_key"],
        account_name=account["market_name"],
    )


def _accounts() -> list:
    return market_repository.list_market_accounts(platform=market_repository.PLATFORM_COUPANG)


def _build_result(fetched: int, new: int, updated: int, errors: list) -> dict:
    if not errors:
        status = "success"
    elif new + updated > 0:
        status = "partial"
    else:
        status = "fail"
    return {
        "status": status,
        "fetched_count": fetched,
        "new_count": new,
        "updated_count": updated,
        "error_count": len(errors),
        "error_message": " / ".join(errors) if errors else None,
    }


def _no_account_result() -> dict:
    message = "등록된 쿠팡 계정이 없습니다. 설정 화면에서 마켓 계정을 먼저 등록해주세요."
    return {"status": "fail", "fetched_count": 0, "new_count": 0, "updated_count": 0, "error_count": 1, "error_message": message}


def _mark_synced(key: str, fetched_count: int) -> None:
    settings_repository.set_setting(f"last_sync_at:{key}", datetime.now().isoformat(timespec="seconds"))
    settings_repository.set_setting(f"last_fetched_count:{key}", str(fetched_count))


def get_last_sync_at(key: str) -> str:
    return settings_repository.get_setting(f"last_sync_at:{key}")


def get_last_fetched_count(key: str):
    value = settings_repository.get_setting(f"last_fetched_count:{key}")
    return int(value) if value is not None else None


# ---------------- 취소/반품 (claim_type: 'CANCEL' 또는 'RETURN') ----------------

def sync_claims(claim_type: str, period_from=None, period_to=None) -> dict:
    accounts = _accounts()
    if not accounts:
        return _no_account_result()

    total_fetched = 0
    total_new = 0
    total_updated = 0
    errors = []

    for account in accounts:
        client = _client_for_account(account)
        try:
            raw_claims = client.fetch_claims(claim_type, period_from, period_to)
        except CoupangApiError as error:
            errors.append(f"[{account['market_name']}] {error}")
            continue

        total_fetched += len(raw_claims)
        for raw in raw_claims:
            raw_json = json.dumps(raw, ensure_ascii=False)
            existing = claims_repository.find_by_receipt(MARKET_NAME, claim_type, raw["receipt_id"])
            if existing:
                claims_repository.update_claim(
                    existing["id"], raw["receipt_status"], raw["reason_category1"], raw["reason_category2"],
                    raw["reason_detail"], raw["complete_confirm_type"], raw["complete_confirm_date"], raw_json,
                )
                total_updated += 1
            else:
                claims_repository.insert_claim(
                    MARKET_NAME, claim_type, raw["receipt_id"], raw["market_order_id"], raw["receipt_status"],
                    raw["reason_category1"], raw["reason_category2"], raw["reason_detail"], raw["requested_at"],
                    raw["complete_confirm_type"], raw["complete_confirm_date"], raw_json, account["id"],
                )
                total_new += 1

    _mark_synced(f"claim:{claim_type}", total_fetched)
    return _build_result(total_fetched, total_new, total_updated, errors)


# ---------------- 교환 ----------------

def sync_exchange_requests(period_from=None, period_to=None) -> dict:
    accounts = _accounts()
    if not accounts:
        return _no_account_result()

    total_fetched = 0
    total_new = 0
    total_updated = 0
    errors = []

    for account in accounts:
        client = _client_for_account(account)
        try:
            raw_exchanges = client.fetch_exchange_requests(period_from, period_to)
        except CoupangApiError as error:
            errors.append(f"[{account['market_name']}] {error}")
            continue

        total_fetched += len(raw_exchanges)
        for raw in raw_exchanges:
            raw_json = json.dumps(raw, ensure_ascii=False)
            existing = exchange_repository.find_by_exchange_id(MARKET_NAME, raw["exchange_id"])
            if existing:
                exchange_repository.update_exchange(existing["id"], raw["status"], raw_json)
                total_updated += 1
            else:
                exchange_repository.insert_exchange(
                    MARKET_NAME, raw["exchange_id"], raw["market_order_id"], raw["status"],
                    raw["requested_at"], raw_json, account["id"],
                )
                total_new += 1

    _mark_synced("exchange", total_fetched)
    return _build_result(total_fetched, total_new, total_updated, errors)


# ---------------- 상품문의 ----------------

def sync_product_inquiries(period_from=None, period_to=None) -> dict:
    accounts = _accounts()
    if not accounts:
        return _no_account_result()

    total_fetched = 0
    total_new = 0
    total_updated = 0
    errors = []

    for account in accounts:
        client = _client_for_account(account)
        try:
            raw_inquiries = client.fetch_product_inquiries(period_from, period_to)
        except CoupangApiError as error:
            errors.append(f"[{account['market_name']}] {error}")
            continue

        total_fetched += len(raw_inquiries)
        for raw in raw_inquiries:
            raw_json = json.dumps(raw, ensure_ascii=False)
            existing = inquiry_repository.find_product_inquiry(MARKET_NAME, raw["inquiry_id"])
            if existing:
                inquiry_repository.update_product_inquiry(
                    existing["id"], raw["answered"], raw_json,
                    seller_product_id=raw.get("seller_product_id"),
                    vendor_item_id=raw.get("vendor_item_id"),
                    order_ids=raw.get("order_ids"),
                    answer_content=raw.get("answer_content"),
                    answered_at=raw.get("answered_at"),
                )
                total_updated += 1
            else:
                inquiry_repository.insert_product_inquiry(
                    MARKET_NAME, raw["inquiry_id"], raw["market_item_id"], raw["content"],
                    raw["inquiry_at"], raw["answered"], raw_json, account["id"],
                    seller_product_id=raw.get("seller_product_id"),
                    vendor_item_id=raw.get("vendor_item_id"),
                    order_ids=raw.get("order_ids"),
                    answer_content=raw.get("answer_content"),
                    answered_at=raw.get("answered_at"),
                )
                total_new += 1

    _mark_synced("product_inquiry", total_fetched)
    return _build_result(total_fetched, total_new, total_updated, errors)


def answer_product_inquiry(inquiry_row_id: int, content: str) -> dict:
    """
    상품문의에 답변을 등록합니다.

    실제로 쿠팡 상품 페이지에 공개 답변이 올라갑니다. 되돌릴 수 없고, 같은
    문의에 두 번 답변하면 쿠팡이 거부합니다. 그래서 이미 답변완료인 문의는
    보내기 전에 여기서 먼저 막습니다.

    성공하면 {"succeeded": True, "message": ...},
    실패하면 {"succeeded": False, "message": 이유} 를 돌려줍니다.
    """
    inquiry = inquiry_repository.get_product_inquiry(inquiry_row_id)
    if not inquiry:
        return {"succeeded": False, "message": "문의를 찾을 수 없습니다."}
    if inquiry["answered"]:
        return {"succeeded": False, "message": "이미 답변이 등록된 문의입니다. 쿠팡은 같은 문의에 두 번 답변할 수 없습니다."}
    if not (content or "").strip():
        return {"succeeded": False, "message": "답변 내용을 입력해주세요."}

    account = market_repository.get_market_account(inquiry["market_account_id"])
    if not account:
        return {"succeeded": False, "message": "이 문의가 어느 상점 것인지 확인할 수 없습니다."}

    # 쿠팡은 답변자 값(replyBy)으로 WING 아이디 "또는" 업체코드(vendorId)를 받아줍니다.
    # 그래서 두 값을 순서대로 시도합니다: 먼저 판매자님이 넣은 WING 아이디, 그게
    # "값이 올바르지 않다"고 거부되면 업체코드로 한 번 더 시도합니다. 업체코드는
    # 주문수집에 이미 쓰고 있어 확실히 맞는 값이라, 정확한 WING 아이디를 못 찾아도
    # 답변이 나갈 수 있습니다.
    wing_id = (account.get("wing_id") or "").strip()
    vendor_id = (account.get("api_vendor_id") or "").strip()

    candidates = []  # (표시이름, 실제 보낼 값)
    if wing_id:
        candidates.append(("WING 아이디", wing_id))
    if vendor_id and vendor_id != wing_id:
        candidates.append(("업체코드", vendor_id))

    if not candidates:
        return {
            "succeeded": False,
            "message": (
                f"'{account['market_name']}' 상점의 WING 아이디가 등록되어 있지 않습니다. "
                "쿠팡이 답변자 아이디를 필수로 요구합니다. "
                "설정 > 마켓 연동 관리에서 입력해주세요."
            ),
        }

    client = _client_for_account(account)
    last_error = None
    for index, (label, value) in enumerate(candidates):
        try:
            result = client.answer_product_inquiry(inquiry["inquiry_id"], content, value)
        except CoupangApiError as error:
            last_error = str(error)
            # "replyBy 값이 올바르지 않다"는 오류일 때만 다음 후보(업체코드)로 넘어갑니다.
            # 이 오류는 답변이 등록되지 않은 상태라, 다른 값으로 다시 보내도 안전합니다.
            if _is_reply_by_error(last_error) and index < len(candidates) - 1:
                continue
            return {"succeeded": False, "message": last_error}

        inquiry_repository.mark_product_inquiry_answered(inquiry_row_id, content)
        note = "" if label == "WING 아이디" else " (WING 아이디가 맞지 않아 업체코드로 등록했습니다)"
        return {"succeeded": True, "message": (result.get("message") or "답변이 등록되었습니다.") + note}

    return {"succeeded": False, "message": last_error or "답변 등록에 실패했습니다."}


def _is_reply_by_error(message: str) -> bool:
    """쿠팡이 '답변자(replyBy) 값이 올바르지 않다'고 거부한 오류인지 판별합니다."""
    text = (message or "").lower()
    return "replyby" in text or ("wing" in text and "incorrect" in text)


# ---------------- 콜센터문의 ----------------

def sync_call_center_inquiries(period_from=None, period_to=None) -> dict:
    accounts = _accounts()
    if not accounts:
        return _no_account_result()

    total_fetched = 0
    total_new = 0
    total_updated = 0
    errors = []

    for account in accounts:
        client = _client_for_account(account)
        try:
            raw_inquiries = client.fetch_call_center_inquiries(period_from, period_to)
        except CoupangApiError as error:
            errors.append(f"[{account['market_name']}] {error}")
            continue

        total_fetched += len(raw_inquiries)
        for raw in raw_inquiries:
            raw_json = json.dumps(raw, ensure_ascii=False)
            existing = inquiry_repository.find_call_center_inquiry(MARKET_NAME, raw["inquiry_id"])
            if existing:
                inquiry_repository.update_call_center_inquiry(
                    existing["id"], raw["inquiry_status"], raw["partner_counseling_status"], raw_json,
                )
                total_updated += 1
            else:
                inquiry_repository.insert_call_center_inquiry(
                    MARKET_NAME, raw["inquiry_id"], raw["market_order_id"], raw["inquiry_status"],
                    raw["partner_counseling_status"], raw["content"], raw["buyer_phone"],
                    raw["inquiry_at"], raw_json, account["id"],
                )
                total_new += 1

    _mark_synced("call_center_inquiry", total_fetched)
    return _build_result(total_fetched, total_new, total_updated, errors)
