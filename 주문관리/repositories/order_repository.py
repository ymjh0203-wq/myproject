# ==========================================================
# 주문 데이터 저장소 (repositories/order_repository.py)
# ----------------------------------------------------------
# 주문(orders, order_items, shipping_information 등) 관련 SQL을 모아두는
# 곳입니다. 화면(ui/*.py)이나 업무로직(services/*.py)에서는 SQL을 직접
# 쓰지 않고, 이 파일의 함수를 호출합니다.
# ==========================================================

from datetime import datetime

import models
from database import get_connection


def _now() -> str:
    """현재 시각을 텍스트로 돌려줍니다. (DB에는 텍스트로 시간을 저장합니다)"""
    return datetime.now().isoformat(timespec="seconds")


def count_by_work_status() -> dict:
    """
    내부 작업 상태(work_status)별 주문 개수를 딕셔너리로 돌려줍니다.
    예: {"신규주문": 12, "발송대기": 8, "배송중": 0, "배송완료": 0, "구매확정": 0}
    """
    counts = {status: 0 for status in models.WORK_STATUS_LIST}

    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT work_status, COUNT(*) AS cnt FROM orders GROUP BY work_status"
        ).fetchall()
        for row in rows:
            if row["work_status"] in counts:
                counts[row["work_status"]] = row["cnt"]
    finally:
        connection.close()

    return counts


def count_by_work_status_since(cutoff_date: str = None) -> dict:
    """
    count_by_work_status()와 같지만, ordered_at(주문일시)이 cutoff_date 이후인
    주문만 셉니다. cutoff_date가 None이면 전체 기간을 셉니다. (홈 화면의
    "최근 N일" 조회 기간 선택에 씁니다)
    """
    counts = {status: 0 for status in models.WORK_STATUS_LIST}

    connection = get_connection()
    try:
        if cutoff_date:
            rows = connection.execute(
                "SELECT work_status, COUNT(*) AS cnt FROM orders WHERE ordered_at >= ? GROUP BY work_status",
                (cutoff_date,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT work_status, COUNT(*) AS cnt FROM orders GROUP BY work_status"
            ).fetchall()
        for row in rows:
            if row["work_status"] in counts:
                counts[row["work_status"]] = row["cnt"]
    finally:
        connection.close()

    return counts


def count_by_work_status_per_market_account() -> list:
    """
    등록된 마켓 계정(상점)마다, 내부 작업 상태(work_status)별 주문 개수를 셉니다.
    홈 화면 맨 아래 "상점별 현황" 표에 씁니다.
    돌려주는 값: [{"market_account_id": int, "market_name": str, "platform": str,
                   "market_id_display": str, "counts": {work_status: int, ...}}, ...]
    """
    connection = get_connection()
    try:
        accounts = connection.execute(
            "SELECT * FROM market_accounts WHERE is_active = 1 ORDER BY id"
        ).fetchall()

        rows = connection.execute(
            """
            SELECT market_account_id, work_status, COUNT(*) AS cnt
            FROM orders
            WHERE market_account_id IS NOT NULL
            GROUP BY market_account_id, work_status
            """
        ).fetchall()
    finally:
        connection.close()

    counts_by_account = {}
    for row in rows:
        counts_by_account.setdefault(row["market_account_id"], {})[row["work_status"]] = row["cnt"]

    results = []
    for account in accounts:
        account = dict(account)
        counts = {status: 0 for status in models.WORK_STATUS_LIST}
        counts.update(counts_by_account.get(account["id"], {}))
        results.append(
            {
                "market_account_id": account["id"],
                "market_name": account["market_name"],
                "platform": account["platform"],
                # "쇼핑몰ID"에 해당하는 정보가 없어서, 연동방식에 따라 가장 가까운
                # 실제 식별값(Vendor ID 또는 로그인 아이디)을 대신 보여줍니다.
                "market_id_display": account["api_vendor_id"] or account["login_id"] or "-",
                "counts": counts,
            }
        )
    return results


def find_by_market_order(market_name: str, market_order_id: str, shipment_box_id: str):
    """
    이미 저장된 같은 주문이 있는지 찾습니다. (중복 저장 방지에 사용)
    같은 주문인지는 "마켓명 + 마켓 주문번호 + 배송번호" 세 가지로 판단합니다.
    없으면 None을 돌려줍니다.
    """
    connection = get_connection()
    try:
        return connection.execute(
            """
            SELECT * FROM orders
            WHERE market_name = ? AND market_order_id = ? AND shipment_box_id = ?
            """,
            (market_name, market_order_id, shipment_box_id),
        ).fetchone()
    finally:
        connection.close()


def insert_order(
    market_name: str,
    market_order_id: str,
    shipment_box_id: str,
    ordered_at: str,
    market_status: str,
    raw_response_json: str,
    work_status: str = models.WORK_STATUS_NEW,
    shipping_fee: int = None,
    settlement_amount: int = None,
    market_account_id: int = None,
    orderer_name: str = None,
    orderer_phone: str = None,
    paid_at: str = None,
    remote_area: bool = None,
    market_delivery_company_name: str = None,
    market_invoice_number: str = None,
) -> int:
    """새 주문을 저장합니다. work_status를 안 주면 '신규주문'으로 시작합니다."""
    now = _now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO orders (
                market_name, market_order_id, shipment_box_id, ordered_at,
                market_status, work_status, first_collected_at, last_updated_at,
                raw_response_json, shipping_fee, settlement_amount, market_account_id,
                orderer_name, orderer_phone, paid_at, remote_area,
                market_delivery_company_name, market_invoice_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                market_name,
                market_order_id,
                shipment_box_id,
                ordered_at,
                market_status,
                work_status,
                now,
                now,
                raw_response_json,
                shipping_fee,
                settlement_amount,
                market_account_id,
                orderer_name,
                orderer_phone,
                paid_at,
                int(remote_area) if remote_area is not None else None,
                market_delivery_company_name,
                market_invoice_number,
            ),
        )
        new_id = cursor.fetchone()["id"]
        connection.commit()
        return new_id
    finally:
        connection.close()


def update_order_from_market(
    order_id: int,
    market_status: str,
    raw_response_json: str,
    shipping_fee: int = None,
    settlement_amount: int = None,
    orderer_name: str = None,
    orderer_phone: str = None,
    paid_at: str = None,
    remote_area: bool = None,
    market_delivery_company_name: str = None,
    market_invoice_number: str = None,
) -> None:
    """
    쿠팡에서 다시 가져온 정보로 기존 주문을 갱신합니다.
    내부 작업 상태(work_status)는 여기서 건드리지 않습니다 (우리 프로그램이 따로 관리).
    """
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE orders
            SET market_status = ?, raw_response_json = ?, last_updated_at = ?,
                shipping_fee = ?, settlement_amount = ?,
                orderer_name = ?, orderer_phone = ?, paid_at = ?, remote_area = ?,
                market_delivery_company_name = ?, market_invoice_number = ?
            WHERE id = ?
            """,
            (
                market_status, raw_response_json, _now(), shipping_fee, settlement_amount,
                orderer_name, orderer_phone, paid_at,
                int(remote_area) if remote_area is not None else None,
                market_delivery_company_name, market_invoice_number,
                order_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def replace_order_items(order_id: int, items: list) -> None:
    """상품 목록을 통째로 지우고 다시 저장합니다. (수정된 상품 정보 반영용)"""
    connection = get_connection()
    try:
        connection.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        for item in items:
            connection.execute(
                """
                INSERT INTO order_items (
                    order_id, market_item_id, product_name, option_name, quantity, sales_amount,
                    seller_product_code, seller_option_code, delivery_charge_type_name, estimated_shipping_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    item.get("market_item_id"),
                    item.get("product_name"),
                    item.get("option_name"),
                    item.get("quantity"),
                    item.get("sales_amount"),
                    item.get("seller_product_code"),
                    item.get("seller_option_code"),
                    item.get("delivery_charge_type_name"),
                    item.get("estimated_shipping_date"),
                ),
            )
        connection.commit()
    finally:
        connection.close()


def upsert_shipping_information(order_id: int, info: dict) -> None:
    """수취인/통관정보를 저장합니다. 이미 있으면 값만 갱신합니다."""
    connection = get_connection()
    try:
        existing = connection.execute(
            "SELECT id FROM shipping_information WHERE order_id = ?", (order_id,)
        ).fetchone()

        fields = (
            info.get("receiver_name"),
            info.get("receiver_phone_raw"),
            info.get("customs_phone"),
            info.get("pccc"),
            info.get("zip_code"),
            info.get("address_basic"),
            info.get("address_detail"),
        )

        if existing:
            connection.execute(
                """
                UPDATE shipping_information
                SET receiver_name = ?, receiver_phone_raw = ?, customs_phone = ?,
                    pccc = ?, zip_code = ?, address_basic = ?, address_detail = ?
                WHERE order_id = ?
                """,
                fields + (order_id,),
            )
        else:
            connection.execute(
                """
                INSERT INTO shipping_information (
                    order_id, receiver_name, receiver_phone_raw, customs_phone,
                    pccc, zip_code, address_basic, address_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (order_id,) + fields,
            )
        connection.commit()
    finally:
        connection.close()


def record_status_history(order_id: int, old_status: str, new_status: str, changed_by: str) -> None:
    """주문 상태가 바뀐 기록을 이력 테이블에 남깁니다."""
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO order_status_history (order_id, changed_at, old_status, new_status, changed_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, _now(), old_status, new_status, changed_by),
        )
        connection.commit()
    finally:
        connection.close()


def record_sync_history(
    mode: str,
    fetched_count: int,
    new_count: int,
    updated_count: int,
    error_count: int,
    status: str,
    error_message: str = None,
    period_from: str = None,
    period_to: str = None,
) -> None:
    """쿠팡 주문 수집을 한 번 실행한 결과를 이력 테이블에 남깁니다."""
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO api_sync_history (
                synced_at, mode, period_from, period_to, fetched_count, new_count,
                updated_count, error_count, status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_now(), mode, period_from, period_to, fetched_count, new_count, updated_count,
             error_count, status, error_message),
        )
        connection.commit()
    finally:
        connection.close()


def update_cs_memo(order_id: int, memo: str) -> None:
    """사용자가 직접 적은 CS메모(고객 특이사항)를 저장합니다."""
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE orders SET cs_memo = ?, last_updated_at = ? WHERE id = ?",
            (memo, _now(), order_id),
        )
        connection.commit()
    finally:
        connection.close()


def update_quickstar_result(order_id: int, order_no: str, invoice: str) -> None:
    """퀵스타 배대지에 접수한 결과(신청번호/운송장번호)를 주문에 저장합니다."""
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE orders
            SET quickstar_order_no = ?, quickstar_invoice = ?, quickstar_submitted_at = ?,
                last_updated_at = ?
            WHERE id = ?
            """,
            (order_no, invoice, _now(), _now(), order_id),
        )
        connection.commit()
    finally:
        connection.close()


def get_customs_error_sms_sent(order_id: int) -> str:
    """이 주문에 통관/우편 오류문구를 마지막으로 자동발송했을 때의 스냅샷 해시를 돌려줍니다."""
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT customs_error_sms_sent FROM shipping_information WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        return row["customs_error_sms_sent"] if row and row["customs_error_sms_sent"] else None
    finally:
        connection.close()


def mark_customs_error_sms_sent(order_id: int, snapshot_hash: str) -> None:
    """통관/우편 오류문구를 자동발송한 뒤, 그때의 스냅샷 해시를 기록합니다(중복발송 방지)."""
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE shipping_information SET customs_error_sms_sent = ? WHERE order_id = ?",
            (snapshot_hash, order_id),
        )
        connection.commit()
    finally:
        connection.close()


def list_orders_by_work_status(work_status: str) -> list:
    """
    특정 내부 작업 상태에 해당하는 주문 목록을 돌려줍니다.
    각 주문에는 상품 목록(items), 수취인/통관정보(shipping), 마켓 계정 이름
    (market_account_name)이 함께 담겨 있습니다.
    """
    connection = get_connection()
    try:
        order_rows = connection.execute(
            """
            SELECT orders.*, market_accounts.market_name AS market_account_name
            FROM orders
            LEFT JOIN market_accounts ON market_accounts.id = orders.market_account_id
            WHERE orders.work_status = ?
            ORDER BY orders.ordered_at DESC
            """,
            (work_status,),
        ).fetchall()

        results = []
        for order_row in order_rows:
            order = dict(order_row)

            item_rows = connection.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
            ).fetchall()
            order["items"] = [dict(row) for row in item_rows]

            shipping_row = connection.execute(
                "SELECT * FROM shipping_information WHERE order_id = ?", (order["id"],)
            ).fetchone()
            order["shipping"] = dict(shipping_row) if shipping_row else None

            results.append(order)

        return results
    finally:
        connection.close()


def list_orders_with_cs_memo() -> list:
    """
    CS메모(고객 특이사항)가 적혀 있는 주문들을 최신순으로 돌려줍니다.
    CS메모관리 화면에서 "내가 적은 메모" 목록을 보여주는 데 씁니다.
    각 주문에는 list_orders_by_work_status와 동일하게 items/shipping/
    market_account_name이 함께 담깁니다.
    """
    connection = get_connection()
    try:
        order_rows = connection.execute(
            """
            SELECT orders.*, market_accounts.market_name AS market_account_name
            FROM orders
            LEFT JOIN market_accounts ON market_accounts.id = orders.market_account_id
            WHERE orders.cs_memo IS NOT NULL AND TRIM(orders.cs_memo) != ''
            ORDER BY orders.last_updated_at DESC, orders.ordered_at DESC
            """,
        ).fetchall()

        results = []
        for order_row in order_rows:
            order = dict(order_row)
            item_rows = connection.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
            ).fetchall()
            order["items"] = [dict(row) for row in item_rows]
            shipping_row = connection.execute(
                "SELECT * FROM shipping_information WHERE order_id = ?", (order["id"],)
            ).fetchone()
            order["shipping"] = dict(shipping_row) if shipping_row else None
            results.append(order)
        return results
    finally:
        connection.close()


def list_active_orders_for_reconcile(market_account_id: int, work_statuses: list, date_from: str, date_to: str) -> list:
    """
    "쿠팡 목록에서 사라졌는지" 확인할 대상 주문을 돌려줍니다.
    지정한 계정의, 지정한 작업상태(신규주문/발송대기 등)이고, 주문일이 [date_from,
    date_to] 범위 안인 주문들입니다. (id, market_order_id, shipment_box_id만)
    date_from/date_to는 'YYYY-MM-DD' 형식입니다. 주문일 앞 10자리와 비교합니다.
    """
    if not work_statuses:
        return []
    placeholders = ",".join("?" for _ in work_statuses)
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT id, market_order_id, shipment_box_id
            FROM orders
            WHERE market_account_id = ?
              AND work_status IN ({placeholders})
              AND substr(ordered_at, 1, 10) BETWEEN ? AND ?
            """,
            (market_account_id, *work_statuses, date_from, date_to),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def update_work_status(order_id: int, new_status: str, changed_by: str = "manual") -> None:
    """주문의 내부 작업 상태를 바꾸고, 변경 이력을 함께 남깁니다."""
    connection = get_connection()
    try:
        old_row = connection.execute(
            "SELECT work_status FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        old_status = old_row["work_status"] if old_row else None

        connection.execute(
            "UPDATE orders SET work_status = ?, last_updated_at = ? WHERE id = ?",
            (new_status, _now(), order_id),
        )
        connection.commit()
    finally:
        connection.close()

    record_status_history(order_id, old_status, new_status, changed_by)


def update_shipping_invoice(order_id: int, delivery_company_code: str, invoice_number: str) -> None:
    """송장(택배사+운송장번호)을 저장하고, 작업 상태를 배송중으로 바꿉니다."""
    now = _now()
    connection = get_connection()
    try:
        old_row = connection.execute(
            "SELECT work_status FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        old_status = old_row["work_status"] if old_row else None

        connection.execute(
            """
            UPDATE orders
            SET delivery_company_code = ?, invoice_number = ?, shipped_at = ?,
                work_status = ?, last_updated_at = ?
            WHERE id = ?
            """,
            (delivery_company_code, invoice_number, now, models.WORK_STATUS_SHIPPING, now, order_id),
        )
        connection.commit()
    finally:
        connection.close()

    record_status_history(order_id, old_status, models.WORK_STATUS_SHIPPING, "manual")


def update_validation_status(
    order_id: int,
    status: str,
    message: str,
    validated_at: str,
    snapshot_hash: str = None,
    customs_phone: str = None,
    name_message: str = None,
    phone_message: str = None,
    pccc_message: str = None,
    zip_message: str = None,
) -> None:
    """검증 결과(통관검증 상태/메시지/일시 + 항목별 메시지)를 shipping_information 표에 저장합니다."""
    connection = get_connection()
    try:
        if customs_phone:
            connection.execute(
                """
                UPDATE shipping_information
                SET validation_status = ?, validation_message = ?, last_validated_at = ?,
                    validation_snapshot_hash = ?, customs_phone = ?,
                    name_check_message = ?, phone_check_message = ?,
                    pccc_check_message = ?, zip_check_message = ?
                WHERE order_id = ?
                """,
                (status, message, validated_at, snapshot_hash, customs_phone,
                 name_message, phone_message, pccc_message, zip_message, order_id),
            )
        else:
            connection.execute(
                """
                UPDATE shipping_information
                SET validation_status = ?, validation_message = ?, last_validated_at = ?,
                    validation_snapshot_hash = ?,
                    name_check_message = ?, phone_check_message = ?,
                    pccc_check_message = ?, zip_check_message = ?
                WHERE order_id = ?
                """,
                (status, message, validated_at, snapshot_hash,
                 name_message, phone_message, pccc_message, zip_message, order_id),
            )
        connection.commit()
    finally:
        connection.close()


def record_customs_validation(
    order_id: int,
    validator_type: str,
    is_success: bool,
    status_code: str,
    message: str,
    retryable: bool,
) -> None:
    """검증을 한 번 시도한 기록을 customs_validations 이력 표에 남깁니다."""
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO customs_validations (
                order_id, validated_at, validator_type, is_success, status_code, message, retryable
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, _now(), validator_type, int(is_success), status_code, message, int(retryable)),
        )
        connection.commit()
    finally:
        connection.close()
