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
    release_stop_status: str = "",
) -> int:
    now = _now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO claims (
                market_name, market_account_id, claim_type, receipt_id, market_order_id,
                receipt_status, release_stop_status, reason_category1, reason_category2, reason_detail,
                requested_at, complete_confirm_type, complete_confirm_date,
                raw_response_json, first_collected_at, last_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                market_name, market_account_id, claim_type, receipt_id, market_order_id,
                receipt_status, release_stop_status, reason_category1, reason_category2, reason_detail,
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
    release_stop_status: str = None,
) -> None:
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE claims
            SET receipt_status = ?, reason_category1 = ?, reason_category2 = ?, reason_detail = ?,
                complete_confirm_type = ?, complete_confirm_date = ?, raw_response_json = ?,
                release_stop_status = COALESCE(?, release_stop_status),
                last_updated_at = ?
            WHERE id = ?
            """,
            (
                receipt_status, reason_category1, reason_category2, reason_detail,
                complete_confirm_type, complete_confirm_date, raw_response_json,
                release_stop_status,
                _now(), claim_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()


# 쿠팡 반품/취소 목록에서 사라진(=더 이상 활성 아님) 건을 정리할 때 쓰는 상태.
# 화면의 CLAIM_COMPLETED_STATUSES(완료)와 같은 값이라, 정리되면 '현재 접수(진행 중)'에서 빠집니다.
_RESOLVED_STATUS = "RETURNS_COMPLETED"


def reconcile_absent_pending(
    claim_type: str, market_account_id, period_from, period_to, seen_receipt_ids, completed_statuses,
) -> int:
    """★정합성 보정: 특정 계정+종류에서 'DB엔 진행 중인데 이번 쿠팡 조회 결과엔 없는' 접수를
    완료(목록에서 사라짐)로 정리하고, 정리한 건수를 돌려줍니다.

    쿠팡 반품/취소 목록 조회는 완료건까지 함께 내려주므로, '완전히 성공한' 조회 결과에
    어떤 접수가 없다면 그건 더 이상 활성 반품/취소가 아니라는 뜻입니다(고객 철회 등).
    수집은 '쿠팡이 돌려준 건'만 갱신하기 때문에, 이렇게 사라진 건은 이 보정이 없으면
    영원히 '진행 중'으로 남아 실제(쿠팡)와 집계가 안 맞습니다.

    ★안전장치: 반드시 '조회가 완전히 성공한(오류 없는) 계정'에만 호출해야 합니다.
      (조회가 일부 실패하면 활성 건을 잘못 완료처리할 수 있으므로) 또한 접수일이 조회
      기간 안인 건만 대상으로 합니다.
    """
    seen = {str(x) for x in (seen_receipt_ids or [])}
    pf = period_from.isoformat() if hasattr(period_from, "isoformat") else str(period_from)
    pt = period_to.isoformat() if hasattr(period_to, "isoformat") else str(period_to)
    completed = tuple(completed_statuses) or (_RESOLVED_STATUS,)

    connection = get_connection()
    try:
        placeholders = ",".join("?" for _ in completed)
        rows = connection.execute(
            f"""
            SELECT id, receipt_id, requested_at, complete_confirm_type
            FROM claims
            WHERE claim_type = ? AND market_account_id = ?
              AND receipt_status NOT IN ({placeholders})
            """,
            (claim_type, market_account_id, *completed),
        ).fetchall()

        to_fix = []
        for row in rows:
            rid = str(row["receipt_id"])
            if rid in seen:
                continue  # 이번 조회에 나온 건(활성) → 건드리지 않음
            req_date = (row["requested_at"] or "")[:10]
            if not (pf <= req_date <= pt):
                continue  # 조회 기간 밖의 건은 대상 아님(안전)
            to_fix.append(row["id"])

        for claim_id in to_fix:
            connection.execute(
                """
                UPDATE claims
                SET receipt_status = ?, complete_confirm_type = ?, complete_confirm_date = ?,
                    last_updated_at = ?
                WHERE id = ?
                """,
                (_RESOLVED_STATUS, "NOT_IN_COUPANG_LIST", _now(), _now(), claim_id),
            )

        # ★출고중지 유령 건 정리: release_stop_status='미처리'인데 이번 쿠팡 조회에 없는(사라진)
        #   접수는, 쿠팡에서 이미 처리/소멸된 것이므로 '처리(소멸)'로 바꿔 '진행 중'에서 뺍니다.
        #   (이게 없으면 쿠팡엔 없는데 앱에만 미처리로 남아 '출고중지완료 처리'가 500 나던 유령 건)
        rs_rows = connection.execute(
            """
            SELECT id, receipt_id, requested_at
            FROM claims
            WHERE claim_type = ? AND market_account_id = ? AND release_stop_status = '미처리'
            """,
            (claim_type, market_account_id),
        ).fetchall()
        rs_fix = []
        for row in rs_rows:
            if str(row["receipt_id"]) in seen:
                continue  # 이번 조회에 나온 활성 건 → 건드리지 않음
            req_date = (row["requested_at"] or "")[:10]
            if not (pf <= req_date <= pt):
                continue  # 조회 기간 밖은 안전상 제외
            rs_fix.append(row["id"])
        for claim_id in rs_fix:
            connection.execute(
                "UPDATE claims SET release_stop_status = ?, last_updated_at = ? WHERE id = ?",
                ("처리(소멸)", _now(), claim_id),
            )

        connection.commit()
        return len(to_fix) + len(rs_fix)
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
