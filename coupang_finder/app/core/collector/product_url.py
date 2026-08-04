from urllib.parse import parse_qs, urlparse


def parse_product_url(url: str) -> dict:
    """쿠팡 상품 URL에서 vendorItemId/itemId를 최대한 뽑아낸다 (없으면 None).

    coupang_product_id는 검색 API 응답의 productId 필드를 그대로 쓰므로 여기서는 다루지 않는다.
    """
    if not url:
        return {"vendor_item_id": None, "item_id": None}
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return {
        "vendor_item_id": query.get("vendorItemId", [None])[0],
        "item_id": query.get("itemId", [None])[0],
    }
