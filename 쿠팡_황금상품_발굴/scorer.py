# ==========================================================
# 종합 점수 계산 (scorer.py)
# ----------------------------------------------------------
# 크무비 필터를 통과한 상품들의 "유망도"를 100점 만점으로 매깁니다.
# API 조회를 못 한 항목(키 없음/오류)은 해당 점수를 0점으로 처리하고,
# 결과표에 "조회 안 됨"이라고 남깁니다. (프로그램이 죽지 않음)
# ==========================================================

import config
import keyword_dictionaries as kd


def _clip(value, low, high):
    return max(low, min(high, value))


def score_search_volume(search_volume):
    """검색량 점수: 기준(6000) 이상일 때만 점수를 주고, 많을수록 가산."""
    if search_volume is None:
        return 0.0
    if search_volume < config.MIN_MONTHLY_SEARCH_VOLUME:
        return 0.0
    # 6000 ~ 50000 구간을 0~만점으로 매핑, 그 이상은 만점 고정
    ratio = (search_volume - config.MIN_MONTHLY_SEARCH_VOLUME) / (50000 - config.MIN_MONTHLY_SEARCH_VOLUME)
    ratio = _clip(ratio, 0, 1)
    return round(ratio * config.SCORE_WEIGHT_SEARCH_VOLUME, 1)


def score_seasonality(variation_coefficient):
    """계절성 점수: 변동계수가 낮을수록(연중 고르게 팔릴수록) 높은 점수."""
    if variation_coefficient is None:
        return 0.0
    ratio = 1 - _clip(variation_coefficient, 0, 1)
    return round(ratio * config.SCORE_WEIGHT_SEASONALITY, 1)


def score_price(price_won):
    """가격 점수: 기준 가격대 안에서, 객단가가 높을수록 가산."""
    low, high = config.MIN_PRICE_WON, config.MAX_PRICE_WON
    ratio = (price_won - low) / (high - low)
    ratio = _clip(ratio, 0, 1)
    return round(ratio * config.SCORE_WEIGHT_PRICE, 1)


def score_size_weight(size_sum_cm, applied_weight_kg):
    """크무비 강도 점수: 기준을 얼마나 초과했는지로 가산 (경쟁 적은 상품일수록 유리)."""
    size_ratio = _clip((size_sum_cm - config.MIN_SIZE_SUM_CM) / config.MIN_SIZE_SUM_CM, 0, 1)
    weight_ratio = _clip((applied_weight_kg - config.MIN_WEIGHT_KG) / config.MIN_WEIGHT_KG, 0, 1)
    combined = (size_ratio + weight_ratio) / 2
    return round(combined * config.SCORE_WEIGHT_SIZE_WEIGHT, 1)


def score_competition(competition_level):
    """경쟁정도 점수: '낮음'일수록 고득점 (경쟁이 적은 키워드 = 진입하기 좋은 키워드)."""
    table = {"낮음": 1.0, "중간": 0.5, "높음": 0.0}
    ratio = table.get(competition_level, 0.0)
    return round(ratio * config.KW_SCORE_WEIGHT_COMPETITION, 1)


def score_keyword_volume(search_volume):
    """키워드 발굴용 검색량 점수 (배점만 다르고 계산 방식은 상품용과 동일)."""
    if search_volume is None or search_volume < config.MIN_MONTHLY_SEARCH_VOLUME:
        return 0.0
    ratio = (search_volume - config.MIN_MONTHLY_SEARCH_VOLUME) / (50000 - config.MIN_MONTHLY_SEARCH_VOLUME)
    ratio = _clip(ratio, 0, 1)
    return round(ratio * config.KW_SCORE_WEIGHT_VOLUME, 1)


def score_keyword_seasonality(variation_coefficient):
    """키워드 발굴용 계절성 점수 (배점만 다르고 계산 방식은 상품용과 동일)."""
    if variation_coefficient is None:
        return 0.0
    ratio = 1 - _clip(variation_coefficient, 0, 1)
    return round(ratio * config.KW_SCORE_WEIGHT_SEASONALITY, 1)


def calc_seasonal_index(monthly_avg, target_month):
    """
    목표 월의 검색 비율이 '연평균'보다 몇 배 높은지를 계산합니다. (계절지수)
    예: 계절지수 2.5 = 그 달이 평소보다 2.5배 더 검색되는 성수기라는 뜻.

    monthly_avg: {1: 12.5, 2: 20.1, ..., 12: 8.0} 형태 (naver_datalab.get_seasonality 결과)
    반환값: 계절지수 (숫자) 또는 데이터가 없으면 None
    """
    if not monthly_avg or target_month not in monthly_avg:
        return None

    values = list(monthly_avg.values())
    year_avg = sum(values) / len(values) if values else 0
    if year_avg == 0:
        return None

    return round(monthly_avg[target_month] / year_avg, 2)


def score_target_month_fit(seasonal_index):
    """
    목표월 적합도 점수: 계절지수가 1배(평균과 같음)면 0점, 3배 이상이면 만점.
    즉, 목표 월에 얼마나 뚜렷하게 검색이 몰리는지로 점수를 매깁니다.
    """
    if seasonal_index is None:
        return 0.0
    ratio = _clip((seasonal_index - 1) / 2, 0, 1)
    return round(ratio * config.KW_SCORE_WEIGHT_SEASONALITY, 1)


def calc_multi_month_analysis(monthly_avg, target_months):
    """
    목표 월이 여러 개(예: [8, 9, 10])일 때, 각 달의 계절지수를 전부 계산하고
    그중 가장 성수기에 가까운(계절지수가 가장 높은) 달을 찾아줍니다.

    데이터랩은 한 번 조회하면 12개월 비율을 전부 돌려주기 때문에,
    API를 추가로 호출하지 않고 이미 받아온 monthly_avg만으로 계산합니다.

    반환값: {
        "월별계절지수": {8: 1.8, 9: 2.3, 10: 1.1},
        "최적월": 9,             # 계절지수가 가장 높은 달 (없으면 None)
        "최적월계절지수": 2.3,
    }
    """
    monthly_indices = {month: calc_seasonal_index(monthly_avg, month) for month in target_months}
    valid = {m: idx for m, idx in monthly_indices.items() if idx is not None}

    if valid:
        best_month = max(valid, key=valid.get)
        best_index = valid[best_month]
    else:
        best_month = None
        best_index = None

    return {
        "월별계절지수": monthly_indices,
        "최적월": best_month,
        "최적월계절지수": best_index,
    }


def calc_keyword_score(search_volume, competition_level, variation_coefficient, monthly_avg=None, target_months=None):
    """
    상품 정보(가격/크기) 없이, 키워드 자체의 검색량/경쟁정도/계절성만으로 점수를 매깁니다.
    find_keywords.py(키워드 발굴 기능)에서 사용합니다.

    target_months(리스트, 예: [8, 9, 10])가 주어지면, "연중 고르게 팔리는지"가 아니라
    "그 달들 중 제일 잘 팔리는 달 기준으로 얼마나 뚜렷하게 몰리는지"로 계절성 점수를 계산합니다.
    """
    s_volume = score_keyword_volume(search_volume)
    s_competition = score_competition(competition_level)

    multi_month = {"월별계절지수": {}, "최적월": None, "최적월계절지수": None}
    if target_months:
        multi_month = calc_multi_month_analysis(monthly_avg, target_months)
        s_season = score_target_month_fit(multi_month["최적월계절지수"])
    else:
        s_season = score_keyword_seasonality(variation_coefficient)

    total = round(s_volume + s_competition + s_season, 1)

    return {
        "검색량점수": s_volume,
        "경쟁정도점수": s_competition,
        "계절성점수": s_season,
        "총점": total,
        "월별계절지수": multi_month["월별계절지수"],
        "최적월": multi_month["최적월"],
        "최적월계절지수": multi_month["최적월계절지수"],
    }


def calc_total_score(search_volume, variation_coefficient, price_won, size_sum_cm, applied_weight_kg):
    """네 가지 점수를 합산해서 총점과 세부 점수를 돌려줍니다."""
    s_volume = score_search_volume(search_volume)
    s_season = score_seasonality(variation_coefficient)
    s_price = score_price(price_won)
    s_size = score_size_weight(size_sum_cm, applied_weight_kg)

    total = round(s_volume + s_season + s_price + s_size, 1)

    return {
        "검색량점수": s_volume,
        "계절성점수": s_season,
        "가격점수": s_price,
        "크무비강도점수": s_size,
        "총점": total,
    }


def calc_final_recommendation(
    naver_score,
    suitability_score,
    sourcing_score,
    risk_grade,
    relevance_score,
    is_informational,
    is_small,
    is_brand,
    is_low_reliability,
):
    """
    지금까지 계산한 점수들(네이버 데이터 기반 총점 + 해외구매대행 적합도 + 중국 소싱 가능성)을
    하나로 합쳐 최종 점수와 S/A/B/C 등급을 매깁니다. find_keywords.py(3단계)에서 사용합니다.

    검색량이 아무리 높아도, 관련성이 낮거나 정보성 키워드거나 소형/브랜드/위험 요소가 있거나
    데이터 신뢰도가 낮으면 S등급은 주지 않습니다. (다른 등급은 순수 점수로 매깁니다)
    """
    weighted = (
        naver_score * config.FINAL_WEIGHT_NAVER_SCORE
        + suitability_score * config.FINAL_WEIGHT_SUITABILITY
        + sourcing_score * config.FINAL_WEIGHT_SOURCING
    )

    risk_needs_review = risk_grade in (kd.RISK_GRADE_NEEDED, kd.RISK_GRADE_HIGH)
    if risk_needs_review:
        weighted -= config.FINAL_PENALTY_RISK

    weighted = _clip(weighted, 0, 100)

    disqualified_from_s = (
        relevance_score < config.RELEVANCE_LOW_THRESHOLD
        or is_informational
        or is_small
        or is_brand
        or suitability_score < config.FINAL_SUITABILITY_MIN_FOR_S
        or risk_needs_review
        or is_low_reliability
    )

    if weighted >= config.FINAL_GRADE_S and not disqualified_from_s:
        grade = "S"
    elif weighted >= config.FINAL_GRADE_A:
        grade = "A"
    elif weighted >= config.FINAL_GRADE_B:
        grade = "B"
    else:
        grade = "C"

    reason = f"종합점수 {round(weighted, 1)}점"
    if disqualified_from_s and weighted >= config.FINAL_GRADE_S:
        reason += " (S등급 제외 조건에 해당되어 등급을 하향 조정함)"

    return {
        "최종추천점수_종합": round(weighted, 1),
        "추천등급": grade,
        "추천근거": reason,
    }
