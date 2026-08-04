def tokenize(product_name: str) -> list[str]:
    """상품명을 공백 기준으로 단어 목록으로 나눈다 (아주 단순한 규칙)."""
    if not product_name:
        return []
    return product_name.strip().split()
