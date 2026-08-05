# ==========================================================
# 취소/반품 저장소 (repositories/claims_repository.py)
# ----------------------------------------------------------
# claims 표(claim_type='CANCEL' 또는 'RETURN') 관련 SQL을 모아두는 곳입니다.
# ==========================================================

from datetime import datetime

from database import get_connection


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def find_by_receipt(market_name: str, claim_type: str, receipt_id: str):
    connection = get_connection()
    try:
        return connection.execute(
            "SELECT * FROM claims WHERE market_name = ? AND claim_type = ? AND receipt_id = ?",
            (market_name, claim_type, receipt_id),
        ).fetchone()
    finally:
        connection.close()


def insert_claim(
    market_name: str,
    claim_type: str,
    receipt_id: str,
    market_order_id: str,
    receipt_status: str,
    reason_category1: str,
    reason_category2: str,
    reason_detail: str,
    requested_at: str,
    complete_confirm_type: str,
    complete_confirm_date: str,
    raw_response_json: str,
    market_account_id: int = None,
) -> int:
    now = _now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO claims (
                market_name, market_account_id, claim_type, receipt_id, market_order_id,
                receipt_status, reason_category1, reason_category2, reason_detail,
                requested_at, complete_confirm_type, complete_confirm_date,
                raw_response_json, first_collected_at, last_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                market_name, market_account_id, claim_type, receipt_id, market_order_id,
                receipt_status, reason_category1, reason_category2, reason_detail,
                requested_at, complete_confirm_type, complete_confirm_date,
                raw_response_json, now, now,
            ),
        )
        new_id = cursor.fetchone()["id"]
        connection.commit()
        return new_id
    finally:
        connection.close()


def update_claim(
    claim_id: int,
    receipt_status: str,
    reason_category1: str,
    reason_category2: str,
    reason_detail: str,
    complete_confirm_type: str,
    complete_confirm_date: str,
    raw_response_json: str,
) -> None:
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE claims
            SET receipt_status = ?, reason_category1 = ?, reason_category2 = ?, reason_detail = ?,
                complete_confirm_type = ?, complete_confirm_date = ?, raw_response_json = ?,
                last_updated_at = ?
            WHERE id = ?
            """,
            (
                receipt_status, reason_category1, reason_category2, reason_detail,
                complete_confirm_type, complete_confirm_date, raw_response_json,
                _now(), claim_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def list_claims(claim_type: str) -> list:
    """지정한 종류(CANCEL/RETURN)의 취소·반품 목록을 상점 이름과 함께 돌려줍니다."""
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT claims.*, market_accounts.market_name AS market_account_name
            FROM claims
            LEFT JOIN market_accounts ON market_accounts.id = claims.market_account_id
            WHERE claims.claim_type = ?
            ORDER BY claims.requested_at DESC
            """,
            (claim_type,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
