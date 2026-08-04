# ==========================================================
# 중국 소싱 가능성 추정 (china_sourcing.py)
# ----------------------------------------------------------
# 1688/타오바오/알리바바에 절대 접속하지 않습니다. 이 파일이 하는 일은 딱 하나입니다:
#   키워드에 포함된 단어로 "중국에서 소싱하기 쉬운 상품인지"를 규칙 기반으로 추정
#
# (참고: 1688/타오바오/알리바바 검색 링크 생성 기능은 번역 없이는 실제로 검색이
#  안 통해서 실효성이 없다고 판단, 제거했습니다.)
# ==========================================================

import config
import keyword_dictionaries as kd


def _clip(value, low, high):
    return max(low, min(high, value))


def _yesno(value):
    return "예" if value else "아니오"


def estimate_sourcing_potential(keyword, is_brand, detected_category=None):
    """
    키워드 텍스트만으로 중국 소싱 가능성을 0~100점, 4단계 등급으로 추정합니다.
    실제 1688/타오바오 데이터는 전혀 없으므로, 결과에는 항상 '중국사이트직접확인필요여부'가 붙습니다.

    detected_category: keyword_screening.py가 이미 감지한 카테고리(예: '캠핑/아웃도어')를 넘겨주면,
    그 카테고리에 속한다는 사실도 (약한) 긍정 신호로 같이 반영합니다.
    """
    matched_positive = [w for w in kd.SOURCING_POSITIVE_WORDS if w in keyword]

    matched_risk_categories = []
    for category, words in kd.SOURCING_RISK_WORDS.items():
        if any(w in keyword for w in words):
            matched_risk_categories.append(category)

    if not matched_positive and not matched_risk_categories and not is_brand and not detected_category:
        return {
            "중국소싱가능성점수_추정": config.SOURCING_EST_BASE_SCORE,
            "소싱가능성등급_추정": kd.SOURCING_GRADE_UNKNOWN,
            "중국소싱판단근거_추정": "소싱 가능성을 암시하는 단어가 없어 추정 불가",
            "중국사이트직접확인필요여부": _yesno(True),
        }

    score = (
        config.SOURCING_EST_BASE_SCORE
        + len(matched_positive) * config.SOURCING_POSITIVE_WORD_BONUS
        - len(matched_risk_categories) * config.SOURCING_RISK_CATEGORY_PENALTY
    )
    if detected_category:
        score += config.SOURCING_CATEGORY_BONUS
    if is_brand:
        score -= config.SOURCING_BRAND_PENALTY
    score = _clip(score, 0, 100)

    if score >= config.SOURCING_GRADE_HIGH:
        grade = kd.SOURCING_GRADE_HIGH
    elif score >= config.SOURCING_GRADE_MID:
        grade = kd.SOURCING_GRADE_MID
    else:
        grade = kd.SOURCING_GRADE_LOW

    reasons = []
    if matched_positive:
        reasons.append(f"일반 공산품/범용 소재 연상단어 포함: {','.join(matched_positive)}")
    if detected_category:
        reasons.append(f"'{detected_category}' 카테고리로 감지됨 (해당 분야는 중국 생산 비중이 높은 편)")
    if matched_risk_categories:
        reasons.append(f"추가 검토 필요 신호: {','.join(matched_risk_categories)}")
    if is_brand:
        reasons.append("브랜드명 포함 (브랜드 종속 상품은 소싱 리스크 높음)")

    return {
        "중국소싱가능성점수_추정": round(score, 1),
        "소싱가능성등급_추정": grade,
        "중국소싱판단근거_추정": " / ".join(reasons),
        "중국사이트직접확인필요여부": _yesno(True),
    }


def evaluate(keyword, is_brand, detected_category=None):
    """
    find_keywords.py의 score_and_enrich()에서 키워드 하나당 한 번 호출하는 진입점입니다.
    """
    return estimate_sourcing_potential(keyword, is_brand, detected_category)
