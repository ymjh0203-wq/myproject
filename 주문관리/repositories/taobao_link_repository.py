# ==========================================================
# 상품별 타오바오 링크 저장소 (repositories/taobao_link_repository.py)
# ----------------------------------------------------------
# 판매자상품코드(seller_product_code)별로 타오바오 구매 링크를 저장합니다.
# 한 상품에 링크 여러 개(2~3개)를 저장할 수 있고(가격비교·예비 등), 저장하면
# 그 상품의 모든 주문에서 바로 타오바오로 이동할 수 있습니다.
# ==========================================================

from datetime import datetime

from database import get_connection


def list_for_code(seller_product_code: str) -> list:
    """한 상품코드의 저장된 링크들을 돌려줍니다(여러 개 가능)."""
    code = (seller_product_code or "").strip()
    if not code:
        return []
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT * FROM taobao_link WHERE seller_product_code = ? ORDER BY id", (code,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        connection.close()


def get_map(codes=None) -> dict:
    """seller_product_code -> [링크,...] (여러 개). codes를 주면 그 코드들만."""
    connection = get_connection()
    try:
        if codes:
            clean = [str(c).strip() for c in codes if str(c or "").strip()]
            if not clean:
                return {}
            ph = ",".join("?" for _ in clean)
            rows = connection.execute(
                f"SELECT * FROM taobao_link WHERE seller_product_code IN ({ph}) ORDER BY id", clean
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM taobao_link ORDER BY id").fetchall()
        out = {}
        for r in rows:
            out.setdefault(r["seller_product_code"], []).append(dict(r))
        return out
    finally:
        connection.close()


def list_by_product_name(product_name: str, exclude_code: str = None) -> list:
    """같은 '상품명'으로 저장된 링크들을 돌려줍니다. (상품코드가 달라도 같은 상품이면 찾기 위함)
    ★쓰임: 상품을 다른 마켓으로 옮기면 쿠팡 판매자상품코드가 새로 바뀌어, 그 코드엔 링크가
      0개가 됩니다. 이때 '같은 상품명'으로 예전에 저장해둔 링크를 찾아 보여주고 새 코드에도
      이어 붙일 수 있게 합니다. exclude_code를 주면 그 코드(현재 상품)는 결과에서 뺍니다."""
    name = (product_name or "").strip()
    if not name:
        return []
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT * FROM taobao_link WHERE TRIM(product_name) = ? ORDER BY id", (name,)
        ).fetchall()
        ex = (exclude_code or "").strip()
        return [dict(r) for r in rows if str(r["seller_product_code"]).strip() != ex]
    finally:
        connection.close()


def list_all() -> list:
    """저장된 모든 링크를 최근 수정순으로(관리 화면용)."""
    connection = get_connection()
    try:
        rows = connection.execute("SELECT * FROM taobao_link ORDER BY updated_at DESC, id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        connection.close()


def add(seller_product_code: str, product_name: str, url: str, label: str = None, memo: str = None) -> None:
    """한 상품코드에 타오바오 링크를 '추가'합니다(기존 링크는 그대로 두고 새로 하나 더)."""
    code = (seller_product_code or "").strip()
    url = (url or "").strip()
    if not code or not url:
        return
    now = datetime.now().isoformat(timespec="seconds")
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO taobao_link (seller_product_code, product_name, url, label, memo, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (code, product_name or "", url, (label or "").strip() or None, memo, now),
        )
        connection.commit()
    finally:
        connection.close()


def update(link_id: int, url: str, label: str = None) -> None:
    """링크 하나(id)의 URL/라벨을 수정합니다."""
    now = datetime.now().isoformat(timespec="seconds")
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE taobao_link SET url = ?, label = ?, updated_at = ? WHERE id = ?",
            ((url or "").strip(), (label or "").strip() or None, now, int(link_id)),
        )
        connection.commit()
    finally:
        connection.close()


def delete(link_id: int) -> None:
    connection = get_connection()
    try:
        connection.execute("DELETE FROM taobao_link WHERE id = ?", (int(link_id),))
        connection.commit()
    finally:
        connection.close()
