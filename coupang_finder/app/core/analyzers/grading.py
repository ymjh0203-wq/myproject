"""검색수 최종 판정 — 설계서 기준 그대로.

0~29: 제외 / 30~999: 일반 유망상품(저장) / 1000 이상: S등급(저장)
"""

GRADE_NORMAL = "일반 유망상품"
GRADE_S = "S등급"


def assess_grade(search_count: int) -> tuple[str, str | None, str | None]:
    """returns (status, grade, exclusion_reason)."""
    if search_count < 30:
        return "제외", None, "검색수 부족"
    if search_count >= 1000:
        return "저장", GRADE_S, None
    return "저장", GRADE_NORMAL, None
