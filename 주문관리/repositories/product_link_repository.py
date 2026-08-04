# ==========================================================
# 상품 링크 캐시 저장소 (repositories/product_link_repository.py)
# ----------------------------------------------------------
# 쿠팡 주문 데이터에는 상품 페이지 주소(productId)가 없고 vendorItemId만
# 있습니다. 올바른 상품 URL과 "쿠팡상품번호"를 만들려면 productId/itemId가
# 필요해서, 상품조회 API로 한 번 알아낸 값을 여기에 저장해 재사용합니다.
# (주문상품 표는 재수집 때마다 지워지므로 여기 따로 보관합니다)
# ==========================================================

from datetime import datetime

from database import get_connection


def get(vendor_item_id: str) -> dict:
    """vendorItemId로 캐시된 productId/itemId를 돌려줍니다. 없으면 None."""
    if not vendor_item_id:
        return None
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT product_id, item_id FROM product_link_cache WHERE vendor_item_id = ?",
            (str(vendor_item_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def save_many(product_id: str, items: list) -> None:
    """
    상품조회 한 번으로 알아낸 그 상품의 모든 옵션(vendorItemId -> itemId)을
    한꺼번에 저장합니다. 이러면 같은 상품의 다른 옵션도 이후엔 API 없이 바로
    링크를 만들 수 있습니다.
    items: [{"vendor_item_id": str, "item_id": str}, ...]
    """
    now = datetime.now().isoformat(timespec="seconds")
    connection = get_connection()
    try:
        for item in items:
            vendor_item_id = item.get("vendor_item_id")
            if not vendor_item_id:
                continue
            connection.execute(
                """
                INSERT INTO product_link_cache (vendor_item_id, product_id, item_id, resolved_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(vendor_item_id) DO UPDATE SET
                    product_id = excluded.product_id,
                    item_id = excluded.item_id,
                    resolved_at = excluded.resolved_at
                """,
                (str(vendor_item_id), str(product_id), str(item.get("item_id") or ""), now),
            )
        connection.commit()
    finally:
        connection.close()
