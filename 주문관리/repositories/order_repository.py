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
    """사용자가 직접 적은 CS메모(고객 특이사항)를 저장합니다. 메모 저장 시각(cs_memo_updated_at)도
    함께 기록해, CS메모관리 화면에서 '메모 작성일'로 보여주고 기간으로 걸러볼 수 있게 합니다.
    (메모를 지우면 작성일도 비웁니다.)"""
    now = _now()
    memo_at = now if (memo or "").strip() else None
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE orders SET cs_memo = ?, cs_memo_updated_at = ?, last_updated_at = ? WHERE id = ?",
            (memo, memo_at, now, order_id),
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


def clear_quickstar_gr(order_id: int) -> None:
    """잘못 넣은 GR신청번호를 지웁니다: quickstar_order_no와 그 GR로 조회했던 가송장
    (quickstar_invoice)까지 함께 비웁니다. (접수시각 등은 건드리지 않음)"""
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE orders SET quickstar_order_no = NULL, quickstar_invoice = NULL, "
            "last_updated_at = ? WHERE id = ?",
            (_now(), order_id),
        )
        connection.commit()
    finally:
        connection.close()


def set_quickstar_invoice(order_id: int, invoice: str) -> None:
    """배대지 조회로 확인한 운송장번호를 주문의 quickstar_invoice 에만 저장합니다.
    (GR신청번호/접수시각 등 다른 값은 건드리지 않습니다.)"""
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE orders SET quickstar_invoice = ?, last_updated_at = ? WHERE id = ?",
            (invoice, _now(), order_id),
        )
        connection.commit()
    finally:
        connection.close()


def list_orders_missing_gr() -> list:
    """GR신청번호(quickstar_order_no)가 없지만 운송장은 있는 주문들을, 매칭에 필요한
    운송장·수취인명과 함께 돌려줍니다. (퀵스타 신청내역으로 GR번호를 백필할 대상)"""
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT o.id, o.market_order_id, o.invoice_number, o.market_invoice_number,
                   s.receiver_name AS receiver_name
            FROM orders o
            LEFT JOIN shipping_information s ON s.order_id = o.id
            WHERE (o.quickstar_order_no IS NULL OR o.quickstar_order_no = '')
              AND ((o.invoice_number IS NOT NULL AND o.invoice_number <> '')
                OR (o.market_invoice_number IS NOT NULL AND o.market_invoice_number <> ''))
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_used_gr_set() -> set:
    """이미 어떤 주문엔가 배정된 GR신청번호(quickstar_order_no) 전체를 집합으로 돌려줍니다.
    (GR 매칭 시 이미 쓴 GR을 후보에서 빼서 중복 배정을 막는 용도)"""
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT DISTINCT quickstar_order_no FROM orders "
            "WHERE quickstar_order_no IS NOT NULL AND quickstar_order_no <> ''"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        connection.close()


def list_ready_to_ship_missing_gr() -> list:
    """발송대기 주문 중 GR신청번호(quickstar_order_no)가 없는 것을, 매칭에 필요한
    수령자명·수령자전화·구매자명·구매자전화와 함께 돌려줍니다. (배대지 접수는 했지만 앱에
    GR이 안 잡힌 발송대기 주문을 이름+전화로 퀵스타 신청내역과 매칭해 백필할 대상.)"""
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT o.id, o.market_order_id,
                   o.orderer_name AS orderer_name, o.orderer_phone AS orderer_phone,
                   s.receiver_name AS receiver_name, s.receiver_phone_raw AS receiver_phone,
                   s.customs_phone AS customs_phone, s.pccc AS pccc,
                   s.zip_code AS zip_code, s.address_basic AS address_basic
            FROM orders o
            LEFT JOIN shipping_information s ON s.order_id = o.id
            WHERE (o.quickstar_order_no IS NULL OR o.quickstar_order_no = '')
              AND o.work_status = ?
            """,
            (models.WORK_STATUS_READY_TO_SHIP,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def backfill_quickstar_order_no(order_id: int, order_no: str, invoice: str = None) -> None:
    """퀵스타 신청내역에서 찾은 GR신청번호를 주문에 채웁니다(백필).
    접수를 지금 한 게 아니므로 quickstar_submitted_at 은 건드리지 않고, quickstar_invoice 는
    비어 있을 때만 채웁니다."""
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE orders
            SET quickstar_order_no = ?,
                quickstar_invoice = COALESCE(NULLIF(quickstar_invoice, ''), ?),
                last_updated_at = ?
            WHERE id = ?
            """,
            (order_no, invoice, _now(), order_id),
        )
        connection.commit()
    finally:
        connection.close()


def record_sms_send(
    order_id: int,
    market_order_id: str,
    phone: str,
    template_key: str,
    kind: str,
    success: bool,
    error_message: str = None,
) -> None:
    """문자 발송 이력을 남깁니다(자동/수동, 성공/실패 모두). 눈으로 확인할 수 있는 흔적용."""
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO sms_send_log
                (order_id, market_order_id, phone, template_key, kind, success, error_message, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id, market_order_id, phone, template_key, kind,
                1 if success else 0, error_message,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def list_sms_sends(order_id: int) -> list:
    """이 주문의 문자 발송 이력을 최신순으로 돌려줍니다(주문 상세에서 보여주기용)."""
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT * FROM sms_send_log WHERE order_id = ? ORDER BY sent_at DESC, id DESC",
            (order_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def list_recent_sms_sends(limit: int = 100) -> list:
    """전체 문자 발송 이력을 최신순으로 돌려줍니다(발송 이력 화면용)."""
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT * FROM sms_send_log ORDER BY sent_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
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


def get_customs_tax_sms_sent(order_id: int) -> str:
    """이 주문에 관부가세 통보(8번) 문구를 마지막으로 자동발송했을 때의 결재통보 시각을 돌려줍니다."""
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT customs_tax_sms_sent FROM shipping_information WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        return row["customs_tax_sms_sent"] if row and row["customs_tax_sms_sent"] else None
    finally:
        connection.close()


def mark_customs_tax_sms_sent(order_id: int, paid_notice_time: str) -> None:
    """관부가세 통보(8번) 문구를 자동발송한 뒤, 그때의 결재통보 처리일시를 기록합니다(중복발송 방지)."""
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE shipping_information SET customs_tax_sms_sent = ? WHERE order_id = ?",
            (paid_notice_time, order_id),
        )
        connection.commit()
    finally:
        connection.close()


def bulk_update_settlement_amount(mapping: dict) -> int:
    """{market_order_id: 실제 정산금액} 을 받아 해당 주문들의 settlement_amount를 갱신합니다.
    (쿠팡 매출내역 조회로 가져온 실제 정산금액을 저장할 때 씁니다.) 갱신된 행 수를 돌려줍니다."""
    if not mapping:
        return 0
    connection = get_connection()
    try:
        updated = 0
        for market_order_id, amount in mapping.items():
            if amount is None:
                continue
            cur = connection.execute(
                "UPDATE orders SET settlement_amount = ? WHERE market_order_id = ?",
                (int(amount), str(market_order_id)),
            )
            updated += cur.rowcount
        connection.commit()
        return updated
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
            ORDER BY COALESCE(orders.cs_memo_updated_at, orders.last_updated_at) DESC, orders.ordered_at DESC
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


def get_full_order_by_market_id(market_order_id: str):
    """마켓 주문번호로 풀 주문(items·shipping 포함)을 하나 돌려줍니다. 없으면 None.
    (취소/반품/교환 클레임 상세에서 원주문 상세내역을 보여줄 때 사용)"""
    connection = get_connection()
    try:
        order_row = connection.execute(
            """
            SELECT orders.*, market_accounts.market_name AS market_account_name
            FROM orders
            LEFT JOIN market_accounts ON market_accounts.id = orders.market_account_id
            WHERE orders.market_order_id = ?
            ORDER BY orders.ordered_at DESC LIMIT 1
            """,
            (str(market_order_id),),
        ).fetchone()
        if not order_row:
            return None
        order = dict(order_row)
        item_rows = connection.execute(
            "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
        ).fetchall()
        order["items"] = [dict(row) for row in item_rows]
        shipping_row = connection.execute(
            "SELECT * FROM shipping_information WHERE order_id = ?", (order["id"],)
        ).fetchone()
        order["shipping"] = dict(shipping_row) if shipping_row else None
        return order
    finally:
        connection.close()


def total_quantity_for_market_order(market_order_id: str) -> int:
    """마켓 주문번호의 총 주문수량(취소 승인 시 cancelCountSum 계산용). 없으면 0."""
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(oi.quantity), 0)
            FROM order_items oi JOIN orders o ON o.id = oi.order_id
            WHERE o.market_order_id = ?
            """,
            (str(market_order_id),),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        connection.close()


def order_info_for_market_ids(market_order_ids: list) -> dict:
    """
    주문번호(market_order_id) 목록으로 주문자명/전화번호/상품명을 한 번에 조회합니다.
    돌려주는 값: {market_order_id: {"name": 주문자명, "phone": 전화번호, "products": "상품명 / 상품명…"}}
    (반품 보상관리에서 클레임에 없는 고객·상품 정보를 붙이는 데 씁니다)
    """
    ids = [str(x) for x in {m for m in market_order_ids if m}]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT o.market_order_id AS moid, o.orderer_name AS name, o.orderer_phone AS phone,
                   GROUP_CONCAT(oi.product_name, ' / ') AS products
            FROM orders o
            LEFT JOIN order_items oi ON oi.order_id = o.id
            WHERE o.market_order_id IN ({placeholders})
            GROUP BY o.id
            """,
            ids,
        ).fetchall()
        out = {}
        for r in rows:
            # 같은 주문번호가 묶음배송으로 여러 행일 수 있으니, 먼저 나온 것을 씁니다.
            if r["moid"] not in out:
                out[r["moid"]] = {"name": r["name"], "phone": r["phone"], "products": r["products"]}
        return out
    finally:
        connection.close()


def list_orders_for_ledger(account_id: int, date_from: str, date_to: str, exclude_closed: bool = True) -> list:
    """
    매출정리용: 한 상점(account_id)의 결제일 [date_from~date_to] 주문을 주문별로 돌려줍니다.
    돌려주는 값: [{order_id, market_order_id, ordered_at, paid_at, customer, product, option, qty, sales, settlement}]
      - sales(총판매금액), settlement(정산금: 실정산 있으면 실값, 없으면 판매−12% 추정)
    date_from/date_to는 'YYYY-MM-DD'. paid_at 앞 10자리와 비교합니다.
    """
    where = "o.market_account_id = ? AND o.paid_at IS NOT NULL AND o.paid_at != '' AND substr(o.paid_at,1,10) BETWEEN ? AND ?"
    params = [account_id, date_from, date_to]
    if exclude_closed:
        where += " AND o.work_status != '주문종료(취소·반품 등)'"
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT o.id AS order_id, o.market_order_id, o.ordered_at, o.paid_at,
                   o.quickstar_order_no AS gr,
                   o.cs_memo AS cs_memo,
                   COALESCE(NULLIF(o.invoice_number, ''), o.market_invoice_number) AS invoice,
                   COALESCE(o.orderer_phone, '') AS phone,
                   s.pccc AS pccc, s.zip_code AS zip_code,
                   s.address_basic AS addr1, s.address_detail AS addr2,
                   COALESCE(o.orderer_name, '') AS customer,
                   (SELECT GROUP_CONCAT(product_name, ' / ') FROM order_items WHERE order_id = o.id) AS product,
                   (SELECT GROUP_CONCAT(option_name, ' / ') FROM order_items WHERE order_id = o.id) AS "option",
                   (SELECT COALESCE(SUM(quantity), 0) FROM order_items WHERE order_id = o.id) AS qty,
                   (SELECT COALESCE(SUM(sales_amount), 0) FROM order_items WHERE order_id = o.id) AS sales,
                   CASE WHEN o.settlement_amount IS NOT NULL THEN o.settlement_amount
                        ELSE (SELECT COALESCE(SUM(sales_amount), 0) FROM order_items WHERE order_id = o.id)
                             - CAST(ROUND((SELECT COALESCE(SUM(sales_amount), 0) FROM order_items WHERE order_id = o.id) * 0.12) AS INTEGER)
                   END AS settlement
            FROM orders o
            LEFT JOIN shipping_information s ON s.order_id = o.id
            WHERE {where}
            ORDER BY o.paid_at
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        connection.close()


def monthly_sales_detailed(exclude_closed: bool = True) -> list:
    """
    결제일(paid_at) 기준으로 '월 × 상점(마켓 계정)'별 매출을 집계합니다.
    돌려주는 값: [{"월","상점","건수","판매금액","수수료","정산금액","배송비"}, ...] (최신월 먼저)
    (전체/마켓별 그래프를 화면에서 나눠 그리기 위한 상세 집계)
    """
    where = "o.paid_at IS NOT NULL AND o.paid_at != ''"
    if exclude_closed:
        where += " AND o.work_status != '주문종료(취소·반품 등)'"
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT ym AS 월,
                   COALESCE(mname, '(미지정)') AS 상점,
                   COUNT(*) AS 건수,
                   SUM(sales) AS 판매금액,
                   SUM(fee) AS 수수료,
                   SUM(sales) - SUM(fee) AS 정산금액,
                   SUM(ship) AS 배송비
            FROM (
                SELECT substr(o.paid_at, 1, 7) AS ym,
                       ma.market_name AS mname,
                       (SELECT COALESCE(SUM(sales_amount), 0) FROM order_items WHERE order_id = o.id) AS sales,
                       CASE WHEN o.settlement_amount IS NOT NULL
                            THEN (SELECT COALESCE(SUM(sales_amount), 0) FROM order_items WHERE order_id = o.id) - o.settlement_amount
                            ELSE CAST(ROUND((SELECT COALESCE(SUM(sales_amount), 0) FROM order_items WHERE order_id = o.id) * 0.12) AS INTEGER)
                       END AS fee,
                       COALESCE(o.shipping_fee, 0) AS ship
                FROM orders o
                LEFT JOIN market_accounts ma ON ma.id = o.market_account_id
                WHERE {where}
            )
            GROUP BY ym, 상점
            ORDER BY ym DESC, 상점
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        connection.close()


def monthly_sales(exclude_closed: bool = True) -> list:
    """
    결제일(paid_at) 기준으로 월별 매출을 집계합니다.
    돌려주는 값: [{"월","건수","판매금액","수수료","정산금액","배송비"}, ...] (최신월 먼저)
      - 판매금액 = 그 달 주문들의 상품 판매금액(order_items.sales_amount) 합
      - 수수료   = 실제 정산분은 (판매−정산), 미정산분은 판매의 12% 추정
      - 정산금액 = 판매금액 − 수수료
    exclude_closed=True면 '주문종료(취소·반품 등)'는 매출에서 제외합니다.
    """
    where = "o.paid_at IS NOT NULL AND o.paid_at != ''"
    if exclude_closed:
        where += " AND o.work_status != '주문종료(취소·반품 등)'"
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT ym AS 월,
                   COUNT(*) AS 건수,
                   SUM(sales) AS 판매금액,
                   SUM(fee) AS 수수료,
                   SUM(sales) - SUM(fee) AS 정산금액,
                   SUM(ship) AS 배송비
            FROM (
                SELECT substr(o.paid_at, 1, 7) AS ym,
                       (SELECT COALESCE(SUM(sales_amount), 0) FROM order_items WHERE order_id = o.id) AS sales,
                       CASE WHEN o.settlement_amount IS NOT NULL
                            THEN (SELECT COALESCE(SUM(sales_amount), 0) FROM order_items WHERE order_id = o.id) - o.settlement_amount
                            ELSE CAST(ROUND((SELECT COALESCE(SUM(sales_amount), 0) FROM order_items WHERE order_id = o.id) * 0.12) AS INTEGER)
                       END AS fee,
                       COALESCE(o.shipping_fee, 0) AS ship
                FROM orders o
                WHERE {where}
            )
            GROUP BY ym
            ORDER BY ym DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        connection.close()


def list_active_orders_for_account(market_account_id: int, work_statuses: list) -> list:
    """
    지정 계정의 활성(신규주문/발송대기) 주문 '전체'를 돌려줍니다(기간 제한 없음).
    reconcile에서 쿠팡 실제 상태와 맞춰 '전진(배송중 등)/종료'를 판단하는 데 씁니다.
    돌려주는 값: [{id, market_order_id, shipment_box_id, work_status, ordered_at}, ...]
    """
    if not work_statuses:
        return []
    placeholders = ",".join("?" for _ in work_statuses)
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT id, market_order_id, shipment_box_id, work_status, ordered_at
            FROM orders
            WHERE market_account_id = ? AND work_status IN ({placeholders})
            """,
            (market_account_id, *work_statuses),
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
                work_status = ?, last_updated_at = ?,
                -- 송장 등록/수정 성공 = 쿠팡이 배송지시(DEPARTURE)로 넘어감. 상태값도 같이
                -- 올려, '배송중인데 market_status=INSTRUCT(상품준비중)' 잔상을 원천 차단.
                -- (이미 배송중/배송완료면 뒤로 안 내림)
                market_status = CASE WHEN market_status IN ('ACCEPT', 'INSTRUCT')
                                     THEN 'DEPARTURE' ELSE market_status END
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
