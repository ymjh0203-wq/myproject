# ==========================================================
# 해외구매대행용 키워드 선별 (keyword_screening.py)
# ----------------------------------------------------------
# find_keywords.py가 모아온 연관키워드 하나하나에 대해, "이게 진짜 해외구매대행용
# 크무비 상품에 가까운가?"를 규칙(사전 매칭) 기반으로 추정합니다.
#
# *** 중요: 이건 진짜 AI가 의미를 이해해서 판단하는 게 아니라, keyword_dictionaries.py에
# 있는 단어 목록과 문자열이 겹치는지만 보는 "규칙 기반 추정"입니다. ***
# - 사전에 없는 단어는 놓칠 수 있습니다. (예: "랜턴"이 사전에 없으면 관련성이 낮게 나올 수 있음)
# - 크무비 등급은 실제 치수/무게 데이터가 전혀 없는 상태에서 글자만 보고 추정한 것입니다.
# - 그래서 결과 컬럼 이름에 전부 "_추정"이 붙어 있고, 실제 네이버 API 데이터(검색량,
#   경쟁정도, 계절성)와는 분명히 구분됩니다.
# ==========================================================

import config
import keyword_dictionaries as kd
import blacklist_filter


def _clip(value, low, high):
    return max(low, min(high, value))


def _yesno(value):
    """True/False를 엑셀에서 바로 읽히는 '예'/'아니오' 문자열로 바꿉니다."""
    return "예" if value else "아니오"


def detect_category(seed):
    """시드 키워드가 keyword_dictionaries.CATEGORY_KEYWORDS 중 어디에 속하는지 추정합니다."""
    seed_norm = seed.replace(" ", "")
    for category, words in kd.CATEGORY_KEYWORDS.items():
        if any(w in seed_norm for w in words):
            return category
    return None


def calc_relevance_score(seed, keyword):
    """
    시드 키워드 하나와 연관키워드 하나 사이의 카테고리 관련성을 0~100점으로 추정합니다.
    규칙: (1) 문자열이 서로 포함되면 가산, (2) 시드가 속한 카테고리의 연관단어가
    키워드에 들어있으면 가산. 둘 다 없으면 관련성 낮음으로 처리합니다.
    """
    seed_norm, kw_norm = seed.replace(" ", ""), keyword.replace(" ", "")
    score = 0
    reasons = []

    if seed_norm and (seed_norm in kw_norm or kw_norm in seed_norm):
        score += config.RELEVANCE_SCORE_SUBSTRING
        reasons.append("시드키워드와 문자열이 겹침")

    category = detect_category(seed)
    matched_words = []
    if category:
        matched_words = [w for w in kd.CATEGORY_KEYWORDS[category] if w in kw_norm]
        if matched_words:
            score += config.RELEVANCE_SCORE_CATEGORY_DICT
            reasons.append(f"'{category}' 카테고리 연관단어 포함: {','.join(matched_words)}")

    score = _clip(score, 0, 100)
    if not reasons:
        reasons.append("시드키워드와의 연관성을 찾지 못함 (사전에 없는 단어일 수 있음)")

    return {
        "카테고리관련성점수_추정": score,
        "감지카테고리_추정": category or "",
        "관련성판단근거_추정": " / ".join(reasons),
    }


def calc_relevance_score_multi(seed_field, keyword):
    """
    원본시드키워드는 '캠핑용품, 등산용품'처럼 콤마로 여러 개일 수 있습니다.
    각 시드로 계산해서 가장 높은 점수를 채택합니다.
    """
    seeds = [s.strip() for s in seed_field.split(",") if s.strip()]
    if not seeds:
        return calc_relevance_score("", keyword)
    return max(
        (calc_relevance_score(s, keyword) for s in seeds),
        key=lambda r: r["카테고리관련성점수_추정"],
    )


def check_informational_intent(keyword):
    """정보성/서비스성 검색어인지(구매 대상 상품이 아닌지) 판단합니다."""
    matched = [s for s in kd.INFO_INTENT_SUFFIXES if s in keyword]
    return {
        "정보성키워드여부_추정": _yesno(len(matched) > 0),
        "정보성판단근거_추정": f"정보성 패턴 포함: {','.join(matched)}" if matched else "",
    }


def check_brand_and_small(keyword, blacklist_terms):
    """
    브랜드명 포함 여부(제외키워드.csv의 '브랜드명' 분류를 재사용, 중복 목록 안 만듦)와
    소형/경량 가능성을 확인합니다.
    """
    brand_terms = [(kw, cat) for kw, cat in blacklist_terms if cat == "브랜드명"]
    is_brand, matched_brand, _ = blacklist_filter.check_blacklist(keyword, brand_terms)

    matched_small = [w for w in kd.SMALL_LIGHT_WORDS if w in keyword]

    return {
        "브랜드포함여부": _yesno(is_brand),
        "매칭브랜드": matched_brand or "",
        "소형저가가능성_추정": _yesno(len(matched_small) > 0),
        "소형저가판단근거_추정": f"소형/경량 연상단어 포함: {','.join(matched_small)}" if matched_small else "",
        "_is_brand": is_brand,          # 내부 계산용 (True/False), CSV에는 안 씀
        "_is_small": len(matched_small) > 0,  # 내부 계산용
    }


def estimate_kmoobi_potential(keyword):
    """
    키워드 텍스트만으로 크무비(대형/고중량) 가능성을 0~100점, 5단계 등급으로 추정합니다.
    실제 치수/무게 데이터는 전혀 없으므로, 결과에는 항상 '실제크기무게확인필요'가 붙습니다.
    """
    matched_bulky = [w for w in kd.BULKY_POSITIVE_WORDS if w in keyword]
    matched_small = [w for w in kd.SMALL_LIGHT_WORDS if w in keyword]

    if not matched_bulky and not matched_small:
        return {
            "크무비가능성점수_추정": config.KMOOBI_EST_BASE_SCORE,
            "크무비가능성등급_추정": kd.KMOOBI_GRADE_UNKNOWN,
            "크무비판단근거_추정": "크기/무게를 암시하는 단어가 없어 추정 불가",
            "실제크기무게확인필요": _yesno(True),
        }

    score = (
        config.KMOOBI_EST_BASE_SCORE
        + len(matched_bulky) * config.KMOOBI_EST_BULKY_WORD_BONUS
        - len(matched_small) * config.KMOOBI_EST_SMALL_WORD_PENALTY
    )
    score = _clip(score, 0, 100)

    if score >= config.KMOOBI_EST_GRADE_HIGH:
        grade = kd.KMOOBI_GRADE_HIGH
    elif score >= config.KMOOBI_EST_GRADE_MID:
        grade = kd.KMOOBI_GRADE_MID
    elif score >= config.KMOOBI_EST_GRADE_NORMAL:
        grade = kd.KMOOBI_GRADE_NORMAL
    else:
        grade = kd.KMOOBI_GRADE_SMALL

    reasons = []
    if matched_bulky:
        reasons.append(f"대형/산업·야외용 연상단어 포함: {','.join(matched_bulky)}")
    if matched_small:
        reasons.append(f"소형/경량 연상단어 포함: {','.join(matched_small)}")

    return {
        "크무비가능성점수_추정": round(score, 1),
        "크무비가능성등급_추정": grade,
        "크무비판단근거_추정": " / ".join(reasons),
        "실제크기무게확인필요": _yesno(True),
    }


def combine_suitability(relevance_score, is_informational, is_brand, is_small, kmoobi_score, kmoobi_grade):
    """
    관련성/정보성/브랜드/소형/크무비 신호를 종합해 해외구매대행 적합 가능성 점수와
    제외·검토 사유를 만듭니다. 아무것도 삭제하지 않고, 이유만 문자열로 기록합니다.
    """
    reasons = []
    if is_informational:
        reasons.append("정보성/서비스성 키워드로 추정됨 (상품 키워드 아님)")
    if is_brand:
        reasons.append("브랜드명 포함 (제외키워드.csv 기준)")
    if relevance_score < config.RELEVANCE_LOW_THRESHOLD:
        reasons.append(f"시드키워드와 관련성 낮음 (추정 {relevance_score}점)")
    if kmoobi_grade == kd.KMOOBI_GRADE_SMALL:
        reasons.append("소형/경량 상품일 가능성 높음")

    base = (
        relevance_score * config.SUITABILITY_WEIGHT_RELEVANCE
        + kmoobi_score * config.SUITABILITY_WEIGHT_KMOOBI
    )
    penalty = 0
    if is_informational:
        penalty += config.SUITABILITY_PENALTY_INFORMATIONAL
    if is_brand:
        penalty += config.SUITABILITY_PENALTY_BRAND
    if is_small:
        penalty += config.SUITABILITY_PENALTY_SMALL

    overall = _clip(base - penalty, 0, 100)

    return {
        "해외구매대행적합도점수_추정": round(overall, 1),
        "제외검토사유": " / ".join(reasons) if reasons else "",
    }


def screen_keyword(seed_field, keyword, blacklist_terms):
    """
    find_keywords.py의 score_and_enrich()에서 키워드 하나당 한 번 호출하는 진입점입니다.
    위 함수들의 결과를 합쳐 하나의 딕셔너리(=CSV에 추가될 컬럼들)로 돌려줍니다.
    """
    relevance = calc_relevance_score_multi(seed_field, keyword)
    info = check_informational_intent(keyword)
    brand_small = check_brand_and_small(keyword, blacklist_terms)
    kmoobi = estimate_kmoobi_potential(keyword)

    suitability = combine_suitability(
        relevance["카테고리관련성점수_추정"],
        info["정보성키워드여부_추정"] == "예",
        brand_small["_is_brand"],
        brand_small["_is_small"],
        kmoobi["크무비가능성점수_추정"],
        kmoobi["크무비가능성등급_추정"],
    )

    merged = {}
    for d in (relevance, info, brand_small, kmoobi, suitability):
        merged.update(d)

    # 내부 계산용 키(밑줄로 시작)는 CSV에 안 나가도록 제거
    merged.pop("_is_brand", None)
    merged.pop("_is_small", None)

    return merged
