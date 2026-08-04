"""옵션 구조 품질 판정 — 깔끔 / 확인 필요 / 번잡.

주의: 쿠팡파트너스 검색 API는 옵션 정보를 제공하지 않는다(확인됨).
그래서 옵션 데이터가 없는 상품은 무조건 '확인 필요'로 분류하고, 실제
옵션 목록을 구할 수 있게 되면(Phase 이후) 이 함수가 바로 쓰인다.
"""

import re

COLOR_WORDS = {
    "블랙", "화이트", "그레이", "레드", "블루", "네이비", "그린", "옐로우",
    "핑크", "브라운", "베이지", "실버", "골드", "카키", "퍼플",
}
SIZE_WORDS = {"S", "M", "L", "XL", "XXL", "XS", "FREE", "프리사이즈"}
NOISE_PATTERN = re.compile(
    r"(신형|구형|업그레이드|고급형|특별형|타입\s?[A-Za-z]|리뉴얼|new|New|NEW|\d{4}년?형?)"
)

MAX_CLEAN_LENGTH = 8
MAX_ACCEPTABLE_LENGTH = 15


def assess_option_quality(
    option_names: list[str] | None,
    option_prices: list[int] | None = None,
) -> tuple[str, str]:
    """returns (quality, basis). quality는 '깔끔' | '확인 필요' | '번잡'."""
    if not option_names:
        return "확인 필요", "옵션 데이터를 아직 확보하지 못함"

    total = len(option_names)
    dictionary_hits = sum(
        1 for name in option_names if name.strip() in COLOR_WORDS or name.strip() in SIZE_WORDS
    )
    dictionary_ratio = dictionary_hits / total
    noise_hits = sum(1 for name in option_names if NOISE_PATTERN.search(name))
    avg_length = sum(len(name) for name in option_names) / total

    price_ratio = None
    if option_prices and len(option_prices) == total and min(option_prices) > 0:
        price_ratio = max(option_prices) / min(option_prices)

    reasons = [
        f"사전 매칭률 {dictionary_ratio:.0%}",
        f"신조어/모델명 패턴 감지 {noise_hits}건",
        f"평균 옵션명 길이 {avg_length:.1f}자",
    ]
    if price_ratio is not None:
        reasons.append(f"최고가/최저가 비율 {price_ratio:.1f}배")

    is_messy = (
        noise_hits > 0
        or dictionary_ratio < 0.3
        or avg_length > MAX_ACCEPTABLE_LENGTH
        or (price_ratio is not None and price_ratio > 3)
    )
    is_clean = (
        noise_hits == 0
        and dictionary_ratio >= 0.7
        and avg_length <= MAX_CLEAN_LENGTH
        and (price_ratio is None or price_ratio <= 1.5)
    )

    basis = ", ".join(reasons)
    if is_messy:
        return "번잡", basis
    if is_clean:
        return "깔끔", basis
    return "확인 필요", basis
