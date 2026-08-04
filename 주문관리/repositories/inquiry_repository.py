# ==========================================================
# 문의 저장소 (repositories/inquiry_repository.py)
# ----------------------------------------------------------
# 상품문의(product_inquiries)와 콜센터문의(call_center_inquiries) 두 표를
# 같이 다룹니다. 긴급/문의관리 화면에서 둘 다 보여줄 때 씁니다.
# ==========================================================

from datetime import datetime

from database import get_connection


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------- 상품문의 ----------------

def find_product_inquiry(market_name: str, inquiry_id: str):
    connection = get_connection()
    try:
        return connection.execute(
            "SELECT * FROM product_inquiries WHERE market_name = ? AND inquiry_id = ?",
            (market_name, inquiry_id),
        ).fetchone()
    finally:
        connection.close()


def insert_product_inquiry(
    market_name: str,
    inquiry_id: str,
    market_item_id: str,
    content: str,
    inquiry_at: str,
    answered: bool,
    raw_response_json: str,
    market_account_id: int = None,
    seller_product_id: str = None,
    vendor_item_id: str = None,
    order_ids: str = None,
    answer_content: str = None,
    answered_at: str = None,
) -> int:
    now = _now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO product_inquiries (
                market_name, market_account_id, inquiry_id, market_item_id, content,
                inquiry_at, answered, raw_response_json, first_collected_at, last_updated_at,
                seller_product_id, vendor_item_id, order_ids, answer_content, answered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (market_name, market_account_id, inquiry_id, market_item_id, content,
             inquiry_at, int(answered), raw_response_json, now, now,
             seller_product_id, vendor_item_id, order_ids, answer_content, answered_at),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def update_product_inquiry(
    row_id: int,
    answered: bool,
    raw_response_json: str,
    seller_product_id: str = None,
    vendor_item_id: str = None,
    order_ids: str = None,
    answer_content: str = None,
    answered_at: str = None,
) -> None:
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE product_inquiries
            SET answered = ?, raw_response_json = ?, last_updated_at = ?,
                seller_product_id = COALESCE(?, seller_product_id),
                vendor_item_id = COALESCE(?, vendor_item_id),
                order_ids = COALESCE(?, order_ids),
                answer_content = COALESCE(NULLIF(?, ''), answer_content),
                answered_at = COALESCE(NULLIF(?, ''), answered_at)
            WHERE id = ?
            """,
            (int(answered), raw_response_json, _now(),
             seller_product_id, vendor_item_id, order_ids, answer_content, answered_at, row_id),
        )
        connection.commit()
    finally:
        connection.close()


def get_product_inquiry(row_id: int) -> dict:
    """상품문의 한 건을 상점 이름까지 붙여서 돌려줍니다."""
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT product_inquiries.*, market_accounts.market_name AS market_account_name
            FROM product_inquiries
            LEFT JOIN market_accounts ON market_accounts.id = product_inquiries.market_account_id
            WHERE product_inquiries.id = ?
            """,
            (row_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def mark_product_inquiry_answered(row_id: int, answer_content: str) -> None:
    """우리가 등록한 답변을 저장하고 '답변완료'로 표시합니다."""
    now = _now()
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE product_inquiries
            SET answered = 1, answer_content = ?, answered_at = ?, last_updated_at = ?
            WHERE id = ?
            """,
            (answer_content, now, now, row_id),
        )
        connection.commit()
    finally:
        connection.close()


def find_product_info(seller_product_id: str, vendor_item_id: str, order_ids: str) -> dict:
    """
    문의에 달린 ID들로 우리 주문 데이터에서 상품명/옵션을 찾아옵니다.

    쿠팡 상품문의 응답에는 상품명이 없고 ID만 들어있어서, 이미 수집해둔 주문의
    상품 정보와 맞춰봅니다. 정확한 순서대로 시도합니다:
      1) vendorItemId - 옵션 단위까지 정확히 일치
      2) 주문번호 - 그 주문에 담긴 상품
      3) sellerProductId - 상품 단위(옵션은 여러 개일 수 있어 참고용)
    셋 다 못 찾으면 None을 돌려줍니다. (아직 주문이 없는 상품에 대한 문의)
    """
    connection = get_connection()
    try:
        if vendor_item_id:
            row = connection.execute(
                "SELECT product_name, option_name FROM order_items WHERE market_item_id = ? LIMIT 1",
                (vendor_item_id,),
            ).fetchone()
            if row:
                return {"product_name": row["product_name"], "option_name": row["option_name"], "matched_by": "옵션"}

        for order_id in [o.strip() for o in (order_ids or "").split(",") if o.strip()]:
            row = connection.execute(
                """
                SELECT order_items.product_name, order_items.option_name
                FROM order_items
                JOIN orders ON orders.id = order_items.order_id
                WHERE orders.market_order_id = ?
                LIMIT 1
                """,
                (order_id,),
            ).fetchone()
            if row:
                return {"product_name": row["product_name"], "option_name": row["option_name"], "matched_by": "주문"}

        if seller_product_id:
            row = connection.execute(
                "SELECT product_name, option_name FROM order_items WHERE seller_product_code = ? LIMIT 1",
                (seller_product_id,),
            ).fetchone()
            if row:
                # 상품 단위로만 맞춘 것이라 옵션은 다른 옵션일 수 있습니다.
                return {"product_name": row["product_name"], "option_name": None, "matched_by": "상품"}

        return None
    finally:
        connection.close()


def list_product_inquiries() -> list:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT product_inquiries.*, market_accounts.market_name AS market_account_name
            FROM product_inquiries
            LEFT JOIN market_accounts ON market_accounts.id = product_inquiries.market_account_id
            ORDER BY product_inquiries.inquiry_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


# ---------------- 콜센터문의 ----------------

def find_call_center_inquiry(market_name: str, inquiry_id: str):
    connection = get_connection()
    try:
        return connection.execute(
            "SELECT * FROM call_center_inquiries WHERE market_name = ? AND inquiry_id = ?",
            (market_name, inquiry_id),
        ).fetchone()
    finally:
        connection.close()


def insert_call_center_inquiry(
    market_name: str,
    inquiry_id: str,
    market_order_id: str,
    inquiry_status: str,
    partner_counseling_status: str,
    content: str,
    buyer_phone: str,
    inquiry_at: str,
    raw_response_json: str,
    market_account_id: int = None,
) -> int:
    now = _now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO call_center_inquiries (
                market_name, market_account_id, inquiry_id, market_order_id, inquiry_status,
                partner_counseling_status, content, buyer_phone, inquiry_at,
                raw_response_json, first_collected_at, last_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (market_name, market_account_id, inquiry_id, market_order_id, inquiry_status,
             partner_counseling_status, content, buyer_phone, inquiry_at,
             raw_response_json, now, now),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def update_call_center_inquiry(row_id: int, inquiry_status: str, partner_counseling_status: str, raw_response_json: str) -> None:
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE call_center_inquiries
            SET inquiry_status = ?, partner_counseling_status = ?, raw_response_json = ?, last_updated_at = ?
            WHERE id = ?
            """,
            (inquiry_status, partner_counseling_status, raw_response_json, _now(), row_id),
        )
        connection.commit()
    finally:
        connection.close()


def list_call_center_inquiries() -> list:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT call_center_inquiries.*, market_accounts.market_name AS market_account_name
            FROM call_center_inquiries
            LEFT JOIN market_accounts ON market_accounts.id = call_center_inquiries.market_account_id
            ORDER BY call_center_inquiries.inquiry_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
