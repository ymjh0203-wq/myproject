# ==========================================================
# 상품 링크 서비스 (services/product_link_service.py)
# ----------------------------------------------------------
# 주문 상품의 vendorItemId만으로는 올바른 쿠팡 상품 페이지 주소를 만들 수
# 없습니다. productId(노출상품ID)와 itemId가 필요한데, 이는 상품조회 API로만
# 알 수 있습니다. 이 서비스는 상세보기를 열 때 그 주문 상품에 대해서만 필요한
# 값을 조회하고, 캐시에 저장해 다음부터는 API 없이 바로 쓰게 합니다.
# ==========================================================

from integrations.coupang_client import CoupangClient
from repositories import market_repository, product_link_repository


def _client_for_account(account: dict) -> CoupangClient:
    return CoupangClient(
        vendor_id=account["api_vendor_id"],
        access_key=account["api_access_key"],
        secret_key=account["api_secret_key"],
        account_name=account["market_name"],
    )


def ensure_links_for_order(order: dict) -> None:
    """
    주문 상세보기를 열 때 호출합니다. 그 주문에 담긴 상품들 중 아직 캐시에 없는
    것만 상품조회 API로 productId/itemId를 알아내 캐시에 저장합니다.
    (실패해도 조용히 넘어갑니다 - 링크 하나 못 만든다고 화면이 멈추면 안 됩니다)
    """
    items = order.get("items") or []
    # 아직 캐시에 없는 vendorItemId만 조회 대상으로 모읍니다.
    to_resolve = []  # (seller_product_id, vendor_item_id)
    for item in items:
        vendor_item_id = item.get("market_item_id")
        seller_product_id = item.get("seller_product_code")
        if not vendor_item_id or not seller_product_id:
            continue
        if product_link_repository.get(vendor_item_id) is None:
            to_resolve.append((seller_product_id, vendor_item_id))

    if not to_resolve:
        return

    account = market_repository.get_market_account(order.get("market_account_id"))
    if not account or account.get("connection_type") != "api" or not account.get("api_vendor_id"):
        return

    client = _client_for_account(account)
    # 같은 상품(seller_product_id)은 한 번만 조회하면 그 상품의 모든 옵션이
    # 캐시되므로, 중복 조회를 피합니다.
    resolved_products = set()
    for seller_product_id, _vendor_item_id in to_resolve:
        if seller_product_id in resolved_products:
            continue
        resolved_products.add(seller_product_id)
        info = client.fetch_seller_product(seller_product_id)
        if info and info.get("items"):
            product_link_repository.save_many(
                info["product_id"], info["items"], product_name=info.get("product_name") or ""
            )


def resolve_inquiry_product(seller_product_id: str, vendor_item_id: str, market_account_id) -> dict:
    """
    상품문의 등에서 '주문에 없는 상품'의 상품명/옵션명을 쿠팡 상품조회로 알아냅니다.
    캐시에 있으면 API 없이 바로 돌려주고, 없으면 한 번 조회해 캐시에 저장합니다.
    반환: {"product_name": str, "option_name": str} 또는 조회 실패 시 None.
    (화면 렌더 중 호출되므로 실패해도 조용히 None을 돌려줍니다)
    """
    if vendor_item_id:
        cached = product_link_repository.get(vendor_item_id)
        if cached and cached.get("product_name"):
            return {"product_name": cached.get("product_name") or "",
                    "option_name": cached.get("item_name") or ""}
    if not seller_product_id:
        return None
    account = market_repository.get_market_account(market_account_id)
    if not account or account.get("connection_type") != "api" or not account.get("api_vendor_id"):
        return None
    try:
        info = _client_for_account(account).fetch_seller_product(seller_product_id)
    except Exception:  # noqa: BLE001 (링크/상품명 하나 못 만든다고 화면이 멈추면 안 됨)
        return None
    if not info or not info.get("items"):
        return None
    product_link_repository.save_many(
        info["product_id"], info["items"], product_name=info.get("product_name") or ""
    )
    option_name = ""
    for it in info["items"]:
        if str(it.get("vendor_item_id")) == str(vendor_item_id):
            option_name = it.get("item_name") or ""
            break
    return {"product_name": info.get("product_name") or "", "option_name": option_name}
