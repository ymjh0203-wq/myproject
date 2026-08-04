# ==========================================================
# 위험도 자동 분류 (risk_classifier.py)
# ----------------------------------------------------------
# 키워드 글자만 보고, 판매하기 전에 검토가 필요할 만한 요소가 있는지 규칙 기반으로 추정합니다.
# (전기제품/배터리/어린이제품/의료건강/식품접촉/인증/파손위험/설치AS/캐릭터IP/브랜드)
#
# *** 무기류/마약류/화학물질 같은 진짜 수입 금지 품목은 이미 blacklist_filter.py가
# 결과에서 완전히 제거하기 때문에, 여기서는 다루지 않습니다. ***
#
# 이것도 규칙(사전 매칭) 기반 추정이라, 실제 인증/통관 여부는 관세청이나 관세사를 통해
# 반드시 별도로 확인하셔야 합니다. 법적 판단을 대신하지 않습니다.
# ==========================================================

import config
import keyword_dictionaries as kd


def classify_risk(keyword, is_brand):
    """
    키워드에 위험 신호 단어가 몇 개 매칭되는지 세어서 5단계 등급을 매깁니다.
    반환값: {"위험요소_추정": "배터리포함가능성, 전기제품가능성", "종합위험도등급_추정": "검토 필요"}
    """
    matched = []
    for category, words in kd.RISK_WORD_CATEGORIES.items():
        if any(w in keyword for w in words):
            matched.append(category)

    if is_brand:
        matched.append("브랜드상표권위험")

    count = len(matched)

    if count == 0:
        grade = kd.RISK_GRADE_LOW
    elif count == 1:
        grade = kd.RISK_GRADE_PARTIAL
    elif count < config.RISK_GRADE_HIGH_COUNT:
        grade = kd.RISK_GRADE_NEEDED
    else:
        grade = kd.RISK_GRADE_HIGH

    return {
        "위험요소_추정": ", ".join(matched),
        "종합위험도등급_추정": grade,
    }
