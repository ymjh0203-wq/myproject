# ==========================================================
# 크무비 필터 (filters.py)
# ----------------------------------------------------------
# "크고 / 무겁고 / 객단가 높은" 상품인지 판별하는 계산기입니다.
# 상품 정보(딕셔너리) 하나를 넣으면, 통과 여부와 계산된 값을 돌려줍니다.
# ==========================================================

import config


def calc_volume_weight_kg(width_cm, depth_cm, height_cm):
    """부피중량(kg)을 계산합니다. 가로x세로x높이 / 6000."""
    return (width_cm * depth_cm * height_cm) / config.VOLUME_WEIGHT_DIVISOR


def check_kmubi(product):
    """
    상품 하나가 크무비 조건을 만족하는지 검사합니다.

    product는 아래 키를 가진 딕셔너리여야 합니다:
        가로_cm, 세로_cm, 높이_cm, 실중량_kg, 판매가_원

    반환값: (통과여부(True/False), 사유 설명 문자열, 계산된 값 딕셔너리)
    """
    width = product["가로_cm"]
    depth = product["세로_cm"]
    height = product["높이_cm"]
    real_weight = product["실중량_kg"]
    price = product["판매가_원"]

    size_sum = width + depth + height
    volume_weight = calc_volume_weight_kg(width, depth, height)
    applied_weight = max(real_weight, volume_weight)  # 실중량과 부피중량 중 큰 값

    reasons = []

    size_ok = size_sum >= config.MIN_SIZE_SUM_CM
    if not size_ok:
        reasons.append(f"부피 미달 (가로+세로+높이={size_sum}cm, 기준 {config.MIN_SIZE_SUM_CM}cm)")

    weight_ok = applied_weight >= config.MIN_WEIGHT_KG
    if not weight_ok:
        reasons.append(f"무게 미달 (적용중량={applied_weight:.1f}kg, 기준 {config.MIN_WEIGHT_KG}kg)")

    price_ok = config.MIN_PRICE_WON <= price <= config.MAX_PRICE_WON
    if not price_ok:
        reasons.append(
            f"가격대 벗어남 (판매가={price:,}원, 기준 {config.MIN_PRICE_WON:,}~{config.MAX_PRICE_WON:,}원)"
        )

    passed = size_ok and weight_ok and price_ok
    reason_text = "크무비 조건 통과" if passed else " / ".join(reasons)

    calculated = {
        "부피합_cm": size_sum,
        "부피중량_kg": round(volume_weight, 1),
        "적용중량_kg": round(applied_weight, 1),
    }

    return passed, reason_text, calculated
