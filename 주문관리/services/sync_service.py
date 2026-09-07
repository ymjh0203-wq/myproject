# ==========================================================
# 쿠팡 주문 수집 업무 로직 (services/sync_service.py)
# ----------------------------------------------------------
# "수집하기" 버튼을 눌렀을 때 실제로 일어나는 일을 담당합니다.
#   1. 등록된 마켓 계정(상점)마다 CoupangClient로 (그 화면 단계에 해당하는
#      상태의) 주문 목록을 가져온다
#   2. 이미 저장된 주문이면 -> 최신 정보로 갱신 + 단계가 더 진행됐으면 우리
#      작업상태도 같이 앞으로 이동시킨다 (뒤로는 되돌리지 않음)
#   3. 처음 보는 주문이면 -> 그 단계 상태로, 어느 계정 것인지 표시해서 저장한다
#   4. 이번 수집 결과를 이력(api_sync_history)에 남긴다
#
# 화면(신규주문/발송대기/배송중/배송완료)마다 "수집하기"를 누르면, 그 화면에
# 해당하는 쿠팡 원본 상태를 직접 조회합니다. 그래서 사용자님이 쿠팡 Wing에서
# 직접 처리한 주문도(예: 우리 앱을 거치지 않고 쿠팡에서 바로 송장을 등록한 경우)
# 각 화면에서 다시 수집하면 알맞은 단계에 나타납니다.
#
# 상점(쿠팡 계정)을 여러 개 등록해두셨으면, "설정 > 마켓 연동 관리"에 등록된
# 모든 쿠팡 계정을 순서대로 돌면서 다 가져옵니다.
# ==========================================================

import json
import time
from datetime import date, datetime, timedelta

import models
from integrations.coupang_client import CoupangApiError, CoupangClient
from repositories import market_repository, order_repository, settings_repository


def _fetch_statuses_with_retry(client, market_statuses, period_from, period_to, attempts=3):
    """
    한 계정의 여러 상태 주문을 가져옵니다. 쿠팡이 일시적 오류(504 서버오류,
    429 호출한도)를 내면 잠깐 쉬었다가 다시 시도합니다. 이 재시도가 없으면
    504 한 번에 그 계정 주문이 통째로 빠져서, 배송중으로 넘어간 주문이 발송대기에
    그대로 남는 문제가 생깁니다.
    """
    last_error = None
    for attempt in range(attempts):
        try:
            raw_orders = []
            for market_status in market_statuses:
                raw_orders.extend(client.fetch_orders_by_status(market_status, period_from, period_to))
            return raw_orders
        except CoupangApiError as error:
            last_error = error
            if getattr(error, "retryable", False) and attempt < attempts - 1:
                time.sleep(3 * (attempt + 1))  # 3초, 6초로 점점 길게 쉬고 재시도
                continue
            raise
    raise last_error

MARKET_NAME = "coupang"
LAST_SYNC_SETTING_KEY_PREFIX = "last_sync_at"
LAST_FETCHED_COUNT_KEY_PREFIX = "last_fetched_count"

# 각 내부 작업 단계가 쿠팡의 어느 원본 상태에 해당하는지 (2026-07-18 공식 문서로 확인).
# 신규주문=결제완료(ACCEPT), 발송대기=상품준비중(INSTRUCT). '발송대기로 이동'을 누르면
# 쿠팡에 상품준비중 처리를 요청해 ACCEPT→INSTRUCT로 넘어갑니다.
# 배송중은 쿠팡에서 DEPARTURE(배송지시)와 DELIVERING(배송중) 두 상태 다 해당합니다.
WORK_STATUS_TO_MARKET_STATUSES = {
    models.WORK_STATUS_NEW: ["ACCEPT"],
    models.WORK_STATUS_READY_TO_SHIP: ["INSTRUCT"],
    models.WORK_STATUS_SHIPPING: ["DEPARTURE", "DELIVERING"],
    models.WORK_STATUS_DELIVERED: ["FINAL_DELIVERY"],
}


def _last_sync_key(work_status: str) -> str:
    return f"{LAST_SYNC_SETTING_KEY_PREFIX}:{work_status}"


def _last_fetched_count_key(work_status: str) -> str:
    return f"{LAST_FETCHED_COUNT_KEY_PREFIX}:{work_status}"


def _client_for_account(account: dict) -> CoupangClient:
    return CoupangClient(
        vendor_id=account["api_vendor_id"],
        access_key=account["api_access_key"],
        secret_key=account["api_secret_key"],
        account_name=account["market_name"],
    )


def _client_for_order(order: dict) -> CoupangClient:
    """주문이 어느 마켓 계정 것인지 찾아서, 그 계정 정보로 클라이언트를 만듭니다."""
    account_id = order.get("market_account_id")
    if account_id:
        account = market_repository.get_market_account(account_id)
        if account:
            return _client_for_account(account)
    # 계정 정보가 없으면(예전 데이터, Mock 등) .env 기본 계정을 씁니다.
    return CoupangClient()


def _maybe_advance_work_status(order_id: int, current_status: str, target_status: str) -> None:
    """
    쿠팡에서 다시 가져온 주문이 이미 더 진행된 단계라면, 우리 작업상태도 같이
    앞으로 이동시킵니다. (뒤로는 절대 되돌리지 않습니다 - 순서가 뒤바뀐 응답으로
    실수로 단계가 되돌아가는 일을 막기 위함)
    """
    if current_status == target_status:
        return
    try:
        current_index = models.WORK_STATUS_LIST.index(current_status)
        target_index = models.WORK_STATUS_LIST.index(target_status)
    except ValueError:
        return
    if target_index > current_index:
        order_repository.update_work_status(order_id, target_status, changed_by="auto")


def _save_one_order(raw_order: dict, target_work_status: str, market_account_id: int) -> str:
    """
    주문 하나를 저장하거나 갱신합니다.
    새로 저장했으면 "new", 이미 있어서 갱신했으면 "updated"를 돌려줍니다.
    """
    market_order_id = raw_order["market_order_id"]
    shipment_box_id = raw_order["shipment_box_id"]
    raw_json = json.dumps(raw_order, ensure_ascii=False)

    existing = order_repository.find_by_market_order(MARKET_NAME, market_order_id, shipment_box_id)

    if existing:
        order_id = existing["id"]
        order_repository.update_order_from_market(
            order_id,
            raw_order["market_status"],
            raw_json,
            shipping_fee=raw_order.get("shipping_fee"),
            settlement_amount=raw_order.get("settlement_amount"),
            orderer_name=raw_order.get("orderer_name"),
            orderer_phone=raw_order.get("orderer_phone"),
            paid_at=raw_order.get("paid_at"),
            remote_area=raw_order.get("remote_area"),
            market_delivery_company_name=raw_order.get("market_delivery_company_name"),
            market_invoice_number=raw_order.get("market_invoice_number"),
        )
        _maybe_advance_work_status(order_id, existing["work_status"], target_work_status)
        result = "updated"
    else:
        order_id = order_repository.insert_order(
            market_name=MARKET_NAME,
            market_order_id=market_order_id,
            shipment_box_id=shipment_box_id,
            ordered_at=raw_order["ordered_at"],
            market_status=raw_order["market_status"],
            raw_response_json=raw_json,
            work_status=target_work_status,
            shipping_fee=raw_order.get("shipping_fee"),
            settlement_amount=raw_order.get("settlement_amount"),
            market_account_id=market_account_id,
            orderer_name=raw_order.get("orderer_name"),
            orderer_phone=raw_order.get("orderer_phone"),
            paid_at=raw_order.get("paid_at"),
            remote_area=raw_order.get("remote_area"),
            market_delivery_company_name=raw_order.get("market_delivery_company_name"),
            market_invoice_number=raw_order.get("market_invoice_number"),
        )
        order_repository.record_status_history(order_id, None, target_work_status, "auto")
        result = "new"

    order_repository.replace_order_items(order_id, raw_order["items"])
    order_repository.upsert_shipping_information(order_id, raw_order)

    return result


def sync_orders_for_stage(work_status: str, period_from=None, period_to=None) -> dict:
    """
    지정한 내부 작업 단계(work_status)에 해당하는 쿠팡 주문을, 등록된 쿠팡
    계정(상점) 전부에서 가져와 저장합니다.
    period_from/period_to(date 객체)를 주면 그 결제일시 범위만 가져옵니다.
    화면에 보여줄 결과 요약(dict)을 돌려줍니다.
    """
    market_statuses = WORK_STATUS_TO_MARKET_STATUSES.get(work_status)
    if not market_statuses:
        raise ValueError(f"'{work_status}' 단계는 쿠팡에서 직접 수집할 수 없습니다.")

    accounts = market_repository.list_market_accounts(platform=market_repository.PLATFORM_COUPANG)

    period_from_text = period_from.isoformat() if period_from else None
    period_to_text = period_to.isoformat() if period_to else None

    if not accounts:
        message = "등록된 쿠팡 계정이 없습니다. 설정 화면에서 마켓 계정을 먼저 등록해주세요."
        order_repository.record_sync_history(
            mode="unknown", fetched_count=0, new_count=0, updated_count=0, error_count=1,
            status="fail", error_message=message, period_from=period_from_text, period_to=period_to_text,
        )
        return {
            "status": "fail", "fetched_count": 0, "new_count": 0, "updated_count": 0,
            "error_count": 1, "error_message": message, "retryable": False,
        }

    total_fetched = 0
    total_new = 0
    total_updated = 0
    total_errors = 0
    error_messages = []
    last_mode = "mock"

    for account in accounts:
        client = _client_for_account(account)
        last_mode = client.mode

        try:
            raw_orders = _fetch_statuses_with_retry(client, market_statuses, period_from, period_to)
        except CoupangApiError as error:
            total_errors += 1
            error_messages.append(f"[{account['market_name']}] {error}")
            continue

        total_fetched += len(raw_orders)
        for raw_order in raw_orders:
            try:
                result = _save_one_order(raw_order, work_status, account["id"])
                if result == "new":
                    total_new += 1
                else:
                    total_updated += 1
            except Exception as error:  # 저장 중 예상 못한 문제가 생겨도 나머지 주문은 계속 처리합니다.
                total_errors += 1
                error_messages.append(f"[{account['market_name']}] {error}")

    if total_errors == 0:
        status = "success"
    elif total_new + total_updated > 0:
        status = "partial"
    else:
        status = "fail"

    error_message = " / ".join(error_messages) if error_messages else None

    order_repository.record_sync_history(
        mode=last_mode,
        fetched_count=total_fetched,
        new_count=total_new,
        updated_count=total_updated,
        error_count=total_errors,
        status=status,
        error_message=error_message,
        period_from=period_from_text,
        period_to=period_to_text,
    )
    settings_repository.set_setting(_last_sync_key(work_status), datetime.now().isoformat(timespec="seconds"))
    # "지금 쿠팡에 실제로 몇 건 있는지"를 저장해둡니다 (우리 DB에 누적된 건수와는
    # 다른 개념 - 이건 방금 쿠팡에 물어봐서 받은 실시간 건수입니다).
    if status != "fail":
        settings_repository.set_setting(_last_fetched_count_key(work_status), str(total_fetched))

    return {
        "status": status,
        "fetched_count": total_fetched,
        "new_count": total_new,
        "updated_count": total_updated,
        "error_count": total_errors,
        "error_message": error_message,
    }


def sync_new_orders(period_from=None, period_to=None) -> dict:
    """신규주문(결제완료) 화면의 '수집하기'용 별칭입니다."""
    return sync_orders_for_stage(models.WORK_STATUS_NEW, period_from, period_to)


# 쿠팡에서 실제로 조회 가능한(수집 가능한) 주문 단계들. 순서대로 둡니다.
# 구매확정은 쿠팡에 판매자용 조회 API가 없어서 제외합니다.
COLLECTABLE_ORDER_STAGES = [
    models.WORK_STATUS_NEW,
    models.WORK_STATUS_READY_TO_SHIP,
    models.WORK_STATUS_SHIPPING,
    models.WORK_STATUS_DELIVERED,
]


def reconcile_active_orders(period_from=None, period_to=None) -> dict:
    """
    신규주문/발송대기 주문을 쿠팡 '실제 상태'와 맞춥니다.
      - 쿠팡에서 이미 다음 단계로 넘어간 주문(예: 발송돼서 배송지시=DEPARTURE) → 그 단계(배송중 등)로 '전진'
      - 쿠팡 목록에서 아예 사라진 주문(취소·반품 등) → '주문종료'

    ⚠️ 넘겨받은 period_from/to는 무시하고, 실제 활성 주문들의 주문일 범위(최대 90일)로
    조회합니다. 그래야 오래되어 수집 기간 밖에 있는 발송대기 주문도 빠짐없이 정리됩니다.
    (예전엔 '사라진 것만 종료'했고 기간도 좁아서, 이미 발송된 주문이 발송대기에 계속 쌓였음)

    안전장치: 계정 조회가 실패(504 등)하면 그 계정은 건드리지 않습니다. 또 조회 범위
    (최대 90일)보다 오래된 주문은 '사라졌다'로 오판하지 않도록 종료하지 않습니다.
    """
    accounts = market_repository.list_market_accounts(platform=market_repository.PLATFORM_COUPANG)
    if not accounts:
        return {"status": "success", "closed_count": 0, "advanced_count": 0, "error_message": None}

    all_statuses = ["ACCEPT", "INSTRUCT", "DEPARTURE", "DELIVERING", "FINAL_DELIVERY"]
    active_stages = [models.WORK_STATUS_NEW, models.WORK_STATUS_READY_TO_SHIP]
    order_rank = {ws: i for i, ws in enumerate(models.WORK_STATUS_LIST)}

    def _order_date(order):
        try:
            return date.fromisoformat((order.get("ordered_at") or "")[:10])
        except (TypeError, ValueError):
            return None

    total_closed = 0
    total_advanced = 0
    errors = []

    for account in accounts:
        active = order_repository.list_active_orders_for_account(account["id"], active_stages)
        if not active:
            continue

        # 조회 기간 = 가장 오래된 활성 주문일 ~ 오늘 (최대 90일 전까지만). 활성 주문은 보통
        # 최근이라 실제 조회량은 작습니다.
        floor = date.today() - timedelta(days=90)
        dates = [d for d in (_order_date(o) for o in active) if d]
        fetch_from = max(min(dates) - timedelta(days=2), floor) if dates else floor
        fetch_to = date.today()

        client = _client_for_account(account)
        try:
            live_orders = _fetch_statuses_with_retry(client, all_statuses, fetch_from, fetch_to)
        except CoupangApiError as error:
            # 이 계정은 조회 실패 → 안전을 위해 건드리지 않습니다.
            errors.append(f"[{account['market_name']}] {error}")
            continue

        status_by_box = {str(o["shipment_box_id"]): o.get("market_status") for o in live_orders}

        for order in active:
            box = str(order["shipment_box_id"])
            current = order["work_status"]
            if box in status_by_box:
                # 쿠팡 실제 상태가 더 진행됐으면 그 단계로 전진(뒤로는 안 감).
                target = models.map_market_status_to_work_status(status_by_box[box], current)
                if order_rank.get(target, -1) > order_rank.get(current, -1):
                    order_repository.update_work_status(order["id"], target, changed_by="auto-reconcile")
                    total_advanced += 1
            else:
                # 쿠팡 목록에 없음: 조회 범위 안이면 취소·사라짐 → 종료.
                # 조회 범위(90일)보다 오래된 주문은 오판 방지를 위해 건드리지 않습니다.
                od = _order_date(order)
                if od is None or od >= fetch_from:
                    order_repository.update_work_status(order["id"], models.WORK_STATUS_CLOSED, changed_by="auto-reconcile")
                    total_closed += 1

    return {
        "status": "partial" if errors else "success",
        "closed_count": total_closed,
        "advanced_count": total_advanced,
        "error_message": " / ".join(errors) if errors else None,
    }


def advance_delivered_orders(progress=None) -> dict:
    """
    배송중(work_status=배송중) 주문 중, 쿠팡에서 이미 배송완료(FINAL_DELIVERY)로
    넘어간 건을 '배송완료' 단계로 전진시킵니다.

    ⚠️ 배송중 화면의 '수집하기'는 DEPARTURE/DELIVERING(배송지시·배송중)만 조회하므로,
    배송완료된 주문은 그 조회에 안 나와 영원히 배송중에 갇힙니다. 이 함수가 그 갇힌
    주문을 정리합니다.

    - 화면의 결제일시 필터와 무관하게, '배송중 주문들의 실제 주문일 범위(최대 120일)'로
      조회하므로 오래되어 갇혀 있던 주문도 빠짐없이 정리됩니다.
    - '전진'만 합니다(뒤로 되돌리거나 임의로 종료하지 않음). 그래서 실수로 데이터를
      잃을 위험이 없습니다.
    - 계정 조회가 실패(504 등)하면 그 계정은 건드리지 않습니다.

    반환: {status, scanned, advanced_count, error_message}
    """
    accounts = market_repository.list_market_accounts(platform=market_repository.PLATFORM_COUPANG)
    if not accounts:
        return {"status": "success", "scanned": 0, "advanced_count": 0, "error_message": None}

    order_rank = {ws: i for i, ws in enumerate(models.WORK_STATUS_LIST)}
    delivered_rank = order_rank.get(models.WORK_STATUS_DELIVERED, -1)

    def _order_date(order):
        try:
            return date.fromisoformat((order.get("ordered_at") or "")[:10])
        except (TypeError, ValueError):
            return None

    total_scanned = 0
    total_advanced = 0
    errors = []

    for acc_i, account in enumerate(accounts):
        shipping = order_repository.list_active_orders_for_account(
            account["id"], [models.WORK_STATUS_SHIPPING]
        )
        if not shipping:
            continue
        total_scanned += len(shipping)

        # 조회 기간 = 가장 오래된 배송중 주문일 ~ 오늘 (최대 120일 전까지만).
        floor = date.today() - timedelta(days=120)
        dates = [d for d in (_order_date(o) for o in shipping) if d]
        fetch_from = max(min(dates) - timedelta(days=2), floor) if dates else floor
        fetch_to = date.today()

        client = _client_for_account(account)
        try:
            delivered = _fetch_statuses_with_retry(client, ["FINAL_DELIVERY"], fetch_from, fetch_to)
        except CoupangApiError as error:
            # 이 계정은 조회 실패 → 안전을 위해 건드리지 않습니다.
            errors.append(f"[{account['market_name']}] {error}")
            continue

        delivered_boxes = {str(o["shipment_box_id"]) for o in delivered}
        for order in shipping:
            if str(order["shipment_box_id"]) in delivered_boxes:
                if delivered_rank > order_rank.get(order["work_status"], -1):
                    order_repository.update_work_status(
                        order["id"], models.WORK_STATUS_DELIVERED, changed_by="auto-advance"
                    )
                    total_advanced += 1

        if progress:
            try:
                progress(acc_i + 1, len(accounts))
            except Exception:  # noqa: BLE001
                pass

    return {
        "status": "partial" if errors else "success",
        "scanned": total_scanned,
        "advanced_count": total_advanced,
        "error_message": " / ".join(errors) if errors else None,
    }


def close_returned_active_orders() -> dict:
    """활성(신규주문/발송대기/배송중) 주문 중 '반품·취소 완료'된 건을 주문종료로 정리합니다.
    (반품/취소가 끝났는데 앱엔 아직 배송중 등으로 남던 데이터 불일치를 없앱니다.)
    반환: {status, closed_count, error_message}"""
    try:
        n = order_repository.close_active_orders_with_completed_claims()
        return {"status": "success", "closed_count": n, "error_message": None}
    except Exception as error:  # noqa: BLE001
        return {"status": "fail", "closed_count": 0, "error_message": str(error)}


def advance_confirmed_orders(min_days_since_order: int = 30, dry_run: bool = False,
                             progress=None) -> dict:
    """
    배송완료(work_status=배송완료) 주문 중, 주문일이 min_days_since_order일보다 오래된
    건을 '구매확정' 단계로 전진시킵니다.

    쿠팡은 배송완료 후 일정 기간이 지나면 구매확정으로 자동 처리됩니다. 다만 쿠팡
    원본에 '배송완료 날짜'가 저장돼 있지 않아(주문일·결제일만 있음), '주문일'을 기준으로
    판단합니다. 쿠팡은 보통 주문→배송완료가 1~2주라, 주문 후 30일이면 배송완료 후
    대략 2주 이상 지난 셈입니다. (기준일은 필요하면 조절 가능)

    - '전진'만 합니다(뒤로 되돌리거나 임의로 종료하지 않음).
    - 쿠팡 API 호출 없이 로컬 DB만으로 즉시 처리됩니다.
    - dry_run=True면 실제로 바꾸지 않고 '넘어갈 건수'만 세어 돌려줍니다(미리보기).

    반환: {status, scanned, would_advance, advanced_count, cutoff, dry_run, error_message}
    """
    order_rank = {ws: i for i, ws in enumerate(models.WORK_STATUS_LIST)}
    confirmed_rank = order_rank.get(models.WORK_STATUS_PURCHASE_CONFIRMED, -1)
    cutoff = (date.today() - timedelta(days=min_days_since_order)).isoformat()

    delivered = order_repository.list_orders_by_work_status(models.WORK_STATUS_DELIVERED)
    scanned = len(delivered)

    to_advance = [
        o["id"] for o in delivered
        if (od := (o.get("ordered_at") or "")[:10]) and od <= cutoff
        and confirmed_rank > order_rank.get(o["work_status"], -1)
    ]

    if not dry_run:
        for i, order_id in enumerate(to_advance):
            order_repository.update_work_status(
                order_id, models.WORK_STATUS_PURCHASE_CONFIRMED, changed_by="auto-confirm"
            )
            if progress:
                try:
                    progress(i + 1, len(to_advance))
                except Exception:  # noqa: BLE001
                    pass

    return {
        "status": "success",
        "scanned": scanned,
        "would_advance": len(to_advance),
        "advanced_count": 0 if dry_run else len(to_advance),
        "cutoff": cutoff,
        "dry_run": dry_run,
        "error_message": None,
    }


def sync_all_order_stages(period_from=None, period_to=None) -> dict:
    """
    주문 4단계(신규주문/발송대기/배송중/배송완료)를 한 번에 다시 수집합니다.

    각 화면의 '수집하기'가 자기 단계만 가져오면, 이미 다음 단계로 넘어간 주문을
    못 잡아서 예전 단계에 그대로 남습니다(예: 배송중으로 넘어갔는데 발송대기에
    계속 보임). 이 함수는 네 단계를 모두 다시 가져와서, 넘어간 주문이 제자리
    단계로 이동하도록 맞춰줍니다. 그래서 어느 화면에서 수집해도 전체가 최신화됩니다.

    단계별 실시간 건수는 sync_orders_for_stage가 각자 저장하므로, 각 화면의
    "쿠팡 기준 현재 N건" 안내도 함께 갱신됩니다.

    돌려주는 값: 전체 합계 + 단계별 결과(stages).
    """
    per_stage = {}
    total_fetched = total_new = total_updated = total_errors = 0
    error_messages = []

    for stage in COLLECTABLE_ORDER_STAGES:
        result = sync_orders_for_stage(stage, period_from, period_to)
        per_stage[stage] = result
        total_fetched += result["fetched_count"]
        total_new += result["new_count"]
        total_updated += result["updated_count"]
        total_errors += result["error_count"]
        if result["error_message"]:
            error_messages.append(f"[{stage}] {result['error_message']}")

    if not error_messages:
        status = "success"
    elif total_fetched > 0 or total_new + total_updated > 0:
        status = "partial"
    else:
        status = "fail"

    return {
        "status": status,
        "fetched_count": total_fetched,
        "new_count": total_new,
        "updated_count": total_updated,
        "error_count": total_errors,
        "error_message": " / ".join(error_messages) if error_messages else None,
        "stages": per_stage,
    }


# 각 작업단계 ↔ 쿠팡 상태 (순서대로). 계정별 한 번에 수집할 때 씁니다.
_STAGE_MARKET_MAP = [
    (models.WORK_STATUS_NEW, ["ACCEPT"]),
    (models.WORK_STATUS_READY_TO_SHIP, ["INSTRUCT"]),
    (models.WORK_STATUS_SHIPPING, ["DEPARTURE", "DELIVERING"]),
    (models.WORK_STATUS_DELIVERED, ["FINAL_DELIVERY"]),
]


def coupang_accounts() -> list:
    """등록된 쿠팡 계정 목록."""
    return market_repository.list_market_accounts(platform=market_repository.PLATFORM_COUPANG)


def sync_settlement_amounts(period_from, period_to) -> dict:
    """
    쿠팡 매출내역(정산) 조회로 각 계정의 '주문별 실제 정산금액'을 가져와 DB에 반영합니다.
    매출인식일(구매확정 또는 배송완료 3일 후) 기준이라, 배송완료·구매확정된 주문에만 값이 옵니다.
    반환: {"status", "fetched": 조회된 주문 수, "updated": DB 반영 행 수, "error_message"}
    """
    mapping = {}
    errors = []
    for account in coupang_accounts():
        client = _client_for_account(account)
        try:
            data = client.fetch_revenue_history(period_from, period_to)
        except Exception as error:  # noqa: BLE001
            errors.append(f"[{account.get('market_name')}] {error}")
            continue
        for market_order_id, info in data.items():
            mapping[market_order_id] = info.get("settlement_amount")

    updated = order_repository.bulk_update_settlement_amount(mapping)
    return {
        "status": "fail" if (errors and not mapping) else "success",
        "fetched": len(mapping),
        "updated": updated,
        "error_message": " / ".join(errors[:4]) if errors else None,
    }


def sync_and_reconcile_account(account: dict, period_from=None, period_to=None) -> dict:
    """
    한 계정의 주문 4단계를 '한 번에' 수집하고, 그 계정에서 사라진(취소·반품 등)
    신규/발송대기 주문을 '주문종료'로 정리합니다.

    속도 개선의 핵심: 예전엔 단계별로 4번 가져오고 '사라진 주문 정리'가 또 전체를
    가져와서 중복이 컸는데, 여기서는 계정별로 한 번에 다 가져오면서 정리까지 합니다.

    돌려주는 값: {"per_stage": {단계: 건수}, "new", "updated", "closed", "errors"}
    """
    client = _client_for_account(account)
    per_stage = {}
    seen_boxes = set()
    new_count = updated_count = closed_count = 0
    errors = []
    all_ok = True

    for work_status, market_statuses in _STAGE_MARKET_MAP:
        try:
            raw_orders = _fetch_statuses_with_retry(client, market_statuses, period_from, period_to)
        except CoupangApiError as error:
            errors.append(f"[{account['market_name']}] {work_status}: {error}")
            all_ok = False
            continue
        per_stage[work_status] = len(raw_orders)
        for raw in raw_orders:
            seen_boxes.add(str(raw["shipment_box_id"]))
            try:
                if _save_one_order(raw, work_status, account["id"]) == "new":
                    new_count += 1
                else:
                    updated_count += 1
            except Exception as error:
                errors.append(f"[{account['market_name']}] {error}")

    # 사라진 주문 정리: 모든 단계 조회가 성공한 계정만(오조회로 멀쩡한 주문을 지우는 것 방지)
    if all_ok:
        date_from = (period_from or datetime.now().date()).isoformat()
        date_to = (period_to or datetime.now().date()).isoformat()
        for order in order_repository.list_active_orders_for_reconcile(
            account["id"], [models.WORK_STATUS_NEW, models.WORK_STATUS_READY_TO_SHIP], date_from, date_to
        ):
            if str(order["shipment_box_id"]) not in seen_boxes:
                order_repository.update_work_status(order["id"], models.WORK_STATUS_CLOSED, changed_by="auto-reconcile")
                closed_count += 1

    return {"per_stage": per_stage, "new": new_count, "updated": updated_count,
            "closed": closed_count, "errors": errors}


def record_stage_live_counts(per_stage_counts: dict) -> None:
    """단계별 '쿠팡 실시간 건수'를 저장합니다(각 화면 상단 배너용)."""
    now = datetime.now().isoformat(timespec="seconds")
    for stage, count in per_stage_counts.items():
        settings_repository.set_setting(_last_sync_key(stage), now)
        settings_repository.set_setting(_last_fetched_count_key(stage), str(count))


def get_last_sync_at(work_status: str = models.WORK_STATUS_NEW) -> str:
    """지정한 단계를 마지막으로 수집한 시각을 돌려줍니다. 한 번도 수집한 적이 없으면 None."""
    return settings_repository.get_setting(_last_sync_key(work_status))


def get_last_fetched_count(work_status: str) -> int | None:
    """
    지정한 단계에 대해, 마지막으로 "수집하기"를 눌렀을 때 쿠팡이 실제로
    돌려준 건수를 돌려줍니다 (우리 DB 누적 건수가 아니라, 그 시점 쿠팡의
    실시간 건수입니다). 한 번도 수집한 적이 없으면 None.
    """
    if work_status not in WORK_STATUS_TO_MARKET_STATUSES:
        return None
    value = settings_repository.get_setting(_last_fetched_count_key(work_status))
    return int(value) if value is not None else None


def _recover_already_advanced_orders(client, pending_orders: list) -> tuple:
    """
    acknowledge(결제완료→상품준비중)가 거부된 주문들을 구제합니다.

    ⚠️ 쿠팡은 새 주문을 자동으로 '상품준비중(INSTRUCT)'으로 넘기는 경우가 많은데,
    그러면 우리가 주문확인(acknowledge)을 눌러도 "배송상태를 변경할 수 없습니다"로
    거부됩니다. 이 주문은 이미 목적 상태(상품준비중=발송대기 이상)라 사실상 성공입니다.
    그래서 쿠팡 실제 상태를 확인해, 이미 상품준비중 이상이면 알맞은 단계로 옮기고
    성공으로 셉니다. 여전히 결제완료(또는 조회 안 됨)면 진짜 실패로 남깁니다.

    돌려주는 값: (구제한 건수, 여전히 실패한 주문 목록)
    """
    if not pending_orders:
        return 0, []

    # 조회 기간: 대기 주문들의 결제일 중 가장 이른 날 ~ 오늘 (없으면 최근 14일)
    paid_dates = []
    for o in pending_orders:
        try:
            paid_dates.append(date.fromisoformat((o.get("paid_at") or o.get("ordered_at") or "")[:10]))
        except (TypeError, ValueError):
            pass
    period_from = (min(paid_dates) - timedelta(days=1)) if paid_dates else (date.today() - timedelta(days=14))
    period_to = date.today()

    try:
        live = _fetch_statuses_with_retry(
            client, ["INSTRUCT", "DEPARTURE", "DELIVERING", "FINAL_DELIVERY"], period_from, period_to
        )
        live_by_id = {o["market_order_id"]: o for o in live}
    except CoupangApiError:
        live_by_id = {}

    recovered = 0
    still_failed = []
    for order in pending_orders:
        found = live_by_id.get(order["market_order_id"])
        if found:
            # 이미 상품준비중 이상 → 실제 상태에 맞는 단계로 갱신(성공 처리).
            target = models.map_market_status_to_work_status(
                found.get("market_status"), models.WORK_STATUS_READY_TO_SHIP
            )
            _save_one_order(found, target, order.get("market_account_id"))
            recovered += 1
        else:
            still_failed.append(order)
    return recovered, still_failed


def move_orders_to_ready_to_ship(orders: list) -> dict:
    """
    선택한 주문들을 발송대기로 넘깁니다. 먼저 쿠팡에 "상품준비중" 처리(acknowledge)를
    요청하고, 성공한 주문을 발송대기로 이동합니다.

    ⚠️ 쿠팡이 새 주문을 이미 자동으로 '상품준비중'으로 넘겨버린 경우 acknowledge가
    "배송상태를 변경할 수 없습니다"로 거부되는데, 이런 주문은 사실 이미 발송대기
    상태이므로 실제 상태를 확인해 조용히 발송대기(이상)로 이동시킵니다. 그래도
    안 되는(진짜 문제) 주문만 실패로 돌려줍니다.

    Mock 모드에서는 실제 쿠팡 요청 없이 전부 성공한 것으로 처리합니다.

    돌려주는 값: {"moved_count": int, "failed": [{"market_order_id": str, "message": str}, ...]}
    """
    orders_by_account_id = {}
    for order in orders:
        orders_by_account_id.setdefault(order.get("market_account_id"), []).append(order)

    moved_count = 0
    failed = []

    for account_orders in orders_by_account_id.values():
        client = _client_for_order(account_orders[0])
        shipment_box_ids = [order["shipment_box_id"] for order in account_orders]

        pending = []          # acknowledge 실패/누락 → 실제 상태 확인 대상
        pending_message = {}  # order id -> 원래 실패 사유(진짜 실패일 때 보여줄 메시지)

        try:
            results = client.acknowledge_orders(shipment_box_ids)
        except CoupangApiError as error:
            # API 호출 자체가 실패 → 전부 실제 상태 확인으로 넘김
            results = None
            for order in account_orders:
                pending.append(order)
                pending_message[order["id"]] = str(error)

        if results is not None:
            result_by_shipment_box = {r["shipment_box_id"]: r for r in results}
            for order in account_orders:
                result = result_by_shipment_box.get(order["shipment_box_id"])
                if result and result["succeeded"]:
                    order_repository.update_work_status(order["id"], models.WORK_STATUS_READY_TO_SHIP, changed_by="manual")
                    moved_count += 1
                else:
                    pending.append(order)
                    pending_message[order["id"]] = (
                        result["message"] if result else "쿠팡 응답에서 이 주문의 처리 결과를 찾을 수 없습니다."
                    )

        # acknowledge 거부/실패 주문 중 '이미 상품준비중 이상'인 것은 구제(성공 처리).
        recovered, still_failed = _recover_already_advanced_orders(client, pending)
        moved_count += recovered
        for order in still_failed:
            failed.append({
                "market_order_id": order["market_order_id"],
                "message": pending_message.get(order["id"], "쿠팡 처리에 실패했습니다."),
            })

    return {"moved_count": moved_count, "failed": failed}


def register_invoice_and_move_to_shipping(
    order: dict, delivery_company_code: str, invoice_number: str, estimated_shipping_date: str
) -> dict:
    """
    주문 하나에 대해 실제로 쿠팡에 송장(택배사+운송장번호)을 등록하고, 성공하면
    우리 프로그램에서도 배송중으로 이동시킵니다.

    돌려주는 값: {"succeeded": bool, "message": str}
    """
    client = _client_for_order(order)

    try:
        result = client.register_invoice(order, delivery_company_code, invoice_number, estimated_shipping_date)
    except CoupangApiError as error:
        return {"succeeded": False, "message": str(error)}

    if result["succeeded"]:
        order_repository.update_shipping_invoice(order["id"], delivery_company_code, invoice_number)

    return result


def update_invoice_for_shipping(order: dict, delivery_company_code: str, invoice_number: str) -> dict:
    """이미 배송중인 주문의 송장(택배사+운송장번호)을 쿠팡에서 수정하고, 성공하면 우리 DB도 갱신합니다.
    돌려주는 값: {"succeeded": bool, "message": str}"""
    client = _client_for_order(order)
    try:
        result = client.update_invoice(order, delivery_company_code, invoice_number)
    except CoupangApiError as error:
        return {"succeeded": False, "message": str(error)}

    if result["succeeded"]:
        order_repository.update_shipping_invoice(order["id"], delivery_company_code, invoice_number)

    return result
