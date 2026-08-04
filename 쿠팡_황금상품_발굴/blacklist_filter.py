# ==========================================================
# 제외 키워드 필터 (blacklist_filter.py)
# ----------------------------------------------------------
# 제외키워드.csv에 적어둔 단어(브랜드명, 무기류, 화학류, 해외직구 금지/제한 품목 등)가
# 상품명이나 검색키워드에 포함되어 있으면 걸러냅니다.
#
# *** 주의 ***
# 제외키워드.csv는 사장님이 직접 채워나가는 참고용 목록입니다.
# 관세청이 정한 공식적인 해외직구 금지/제한 품목의 전체 목록이 아니므로,
# 실제 통관 가능 여부는 반드시 관세청 통관정보포털(unipass.customs.go.kr)이나
# 관세사를 통해 별도로 확인하셔야 합니다. 이 필터는 "실수로 놓치는 걸 줄여주는"
# 보조 도구일 뿐, 법적 판단을 대신하지 않습니다.
# ==========================================================

import os

import csv_utils


def load_blacklist(csv_path):
    """
    제외키워드.csv를 읽어옵니다.
    반환값: [(제외키워드, 분류), ...] 리스트. 파일이 없으면 빈 리스트.
    """
    if not os.path.exists(csv_path):
        print(f"[안내] 제외키워드 파일이 없어 브랜드/금지품목 필터 없이 진행합니다: {csv_path}")
        return []

    try:
        rows = csv_utils.read_csv_rows(csv_path)
    except (UnicodeDecodeError, UnicodeError):
        print(f"[오류] '{csv_path}' 파일의 글자 인코딩을 인식하지 못해 제외키워드 필터 없이 진행합니다.")
        print("      엑셀에서 '다른 이름으로 저장' -> 파일 형식을 'CSV UTF-8(쉼표로 분리)'로 다시 저장해주세요.")
        return []

    terms = []
    for row in rows:
        keyword = (row.get("제외키워드") or "").strip()
        category = (row.get("분류") or "").strip()
        if keyword:
            terms.append((keyword, category))
    return terms


def check_blacklist(text, blacklist_terms):
    """
    text(상품명 또는 키워드) 안에 제외키워드가 포함되어 있는지 검사합니다.
    대소문자, 공백은 무시하고 비교합니다.

    반환값: (걸림여부(True/False), 걸린 키워드, 분류)
    """
    if not text or not blacklist_terms:
        return False, None, None

    normalized = text.replace(" ", "").upper()

    for keyword, category in blacklist_terms:
        keyword_normalized = keyword.replace(" ", "").upper()
        if keyword_normalized and keyword_normalized in normalized:
            return True, keyword, category

    return False, None, None
