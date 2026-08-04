# ==========================================================
# 교환요청 저장소 (repositories/exchange_repository.py)
# ==========================================================

from datetime import datetime

from database import get_connection


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def find_by_exchange_id(market_name: str, exchange_id: str):
    connection = get_connection()
    try:
        return connection.execute(
            "SELECT * FROM exchange_requests WHERE market_name = ? AND exchange_id = ?",
            (market_name, exchange_id),
        ).fetchone()
    finally:
        connection.close()


def insert_exchange(
    market_name: str,
    exchange_id: str,
    market_order_id: str,
    status: str,
    requested_at: str,
    raw_response_json: str,
    market_account_id: int = None,
) -> int:
    now = _now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO exchange_requests (
                market_name, market_account_id, exchange_id, market_order_id, status,
                requested_at, raw_response_json, first_collected_at, last_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (market_name, market_account_id, exchange_id, market_order_id, status, requested_at, raw_response_json, now, now),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def update_exchange(exchange_id_pk: int, status: str, raw_response_json: str) -> None:
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE exchange_requests SET status = ?, raw_response_json = ?, last_updated_at = ? WHERE id = ?",
            (status, raw_response_json, _now(), exchange_id_pk),
        )
        connection.commit()
    finally:
        connection.close()


def list_exchanges() -> list:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT exchange_requests.*, market_accounts.market_name AS market_account_name
            FROM exchange_requests
            LEFT JOIN market_accounts ON market_accounts.id = exchange_requests.market_account_id
            ORDER BY exchange_requests.requested_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
