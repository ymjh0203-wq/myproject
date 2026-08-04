"""검색수 확인 우선순위 추정 — 검색수 자체를 대신하지 않는다.

파트너스 API가 공식으로 주는 search_rank(그 키워드 검색결과 안에서 몇 번째로
나왔는지)를 근거로 쓴다. 랭킹이 높을수록(숫자가 작을수록) 쿠팡 자체 알고리즘이
그 상품을 관련성/인기 순으로 앞에 뒀다는 뜻이라, 실제 수요가 있을 가능성이
상대적으로 높다고 보는 보수적인 가정이다. 이 점수는 "먼저 확인할 상품 순서"를
정하는 참고용일 뿐, 실제 검색수를 확정하지 않는다.
"""


def estimate_priority(search_rank: int | None) -> int:
    """0~100점. search_rank가 없으면 중립값(50)을 준다."""
    if search_rank is None or search_rank <= 0:
        return 50
    score = 100 - (search_rank - 1) * 2
    return max(0, min(100, score))
