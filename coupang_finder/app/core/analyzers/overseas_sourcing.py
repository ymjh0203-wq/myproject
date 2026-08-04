"""해외구매대행 가능성 점수 — Boolean이 아니라 0~100점 점수 + 근거.

주의: 배송기간, 판매자 상세 특징, 상세페이지의 중국어 흔적 같은 더 확실한 신호는
상품 상세페이지를 직접 봐야 알 수 있어(확인 필요) 이 모듈에 아직 넣지 않았다.
지금은 API로 확보 가능한 신호(상품명 텍스트, 로켓배송 여부)만 사용하는
보수적인 버전이며, 이 점수만으로 상품을 자동 제외하지 않는다 — 참고용 정보로만 쓴다.
"""

OVERSEAS_HINT_WORDS = ["해외", "구매대행", "직구", "해외배송", "역직구"]
DOMESTIC_HINT_WORDS = ["국내생산", "국산", "당일발송", "국내제작"]


def assess_overseas_sourcing(product_name: str, is_rocket: bool | None = None) -> tuple[int, str]:
    """returns (score 0~100, basis). 점수는 확률적 추정치일 뿐, 확정 판단이 아니다."""
    reasons = []
    score = 40  # 확실한 신호가 없을 때의 중립 기본값

    if is_rocket:
        return 0, "로켓배송 상품 — 해외구매대행일 가능성 매우 낮음"

    hit_overseas = [w for w in OVERSEAS_HINT_WORDS if w in product_name]
    if hit_overseas:
        score += 30
        reasons.append(f"상품명에 해외소싱 관련 표현 포함: {', '.join(hit_overseas)}")

    hit_domestic = [w for w in DOMESTIC_HINT_WORDS if w in product_name]
    if hit_domestic:
        score -= 30
        reasons.append(f"상품명에 국내생산 관련 표현 포함: {', '.join(hit_domestic)}")

    if not hit_overseas and not hit_domestic:
        reasons.append("상품명만으로는 뚜렷한 신호 없음 (배송기간·판매자 특징 등은 상세페이지 확인 필요)")

    score = max(0, min(100, score))
    return score, "; ".join(reasons)
