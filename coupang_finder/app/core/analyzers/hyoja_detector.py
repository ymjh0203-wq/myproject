"""효자상품(판매자 고유 조어) 판정.

주의: 첫 단어를 무조건 제거하는 방식이 아니다. 첫 단어가 사전에 없다는 이유만으로
바로 제외하지 않고, "동일 판매자가 반복해서 쓰는 정도"까지 함께 봐서
정상 / 효자상품 의심 / 제외 중 하나로 판정한다. 데이터가 쌓이기 전(초기)에는
자동 제외 대신 '의심'에 머무르는 쪽이 기본값이다 (오탐으로 좋은 상품을
잃는 것보다, 사람이 한 번 더 보는 편이 안전하기 때문).
"""

from app.db import repository

from .text_utils import tokenize


def assess_hyoja(
    product_name: str,
    seller_name: str | None,
    auto_exclude_repeat_threshold: int = 5,
) -> tuple[str, str, str | None, str]:
    """returns (status, basis, suspected_prefix, core_keyword).

    status: '정상' | '효자상품 의심' | '제외'
    core_keyword: 접두어를 뺀 나머지 상품명 (의심/제외일 때만 의미 있음)
    """
    tokens = tokenize(product_name)
    if not tokens:
        return "정상", "상품명이 비어 있음", None, product_name

    first = tokens[0]
    word_type = repository.lookup_word_type(first)

    if word_type == "브랜드":
        return "정상", f"첫 단어 '{first}'는 브랜드 사전 단어 (브랜드 필터에서 별도 처리됨)", None, product_name

    if word_type in ("일반명사", "속성어"):
        return "정상", f"첫 단어 '{first}'가 {word_type} 사전에 등재되어 있음", None, product_name

    core_keyword = " ".join(tokens[1:]) if len(tokens) > 1 else product_name
    repeat_count = repository.count_seller_prefix_repeat(seller_name, first)

    if repeat_count >= auto_exclude_repeat_threshold:
        return (
            "제외",
            f"'{first}'가 사전에 없고, 동일 판매자({seller_name}) 상품 {repeat_count}건에서 반복 확인됨",
            first,
            core_keyword,
        )

    return (
        "효자상품 의심",
        f"'{first}'가 사전에 없음 (동일 판매자 반복 {repeat_count}건 — 아직 판단 근거 부족, 사람 확인 필요)",
        first,
        core_keyword,
    )
