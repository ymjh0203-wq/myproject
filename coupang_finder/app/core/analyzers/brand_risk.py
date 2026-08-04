"""브랜드/상표권 위험 판정.

주의: 이 모듈은 "상표권 침해 여부"를 법적으로 확정하지 않는다.
안전 / 주의 / 제외 3단계로만 표시하고, 최종 판단은 사람이 한다.

로컬 사전에 없는 단어는 (KIPRIS_SERVICE_KEY가 설정되어 있으면) KIPRIS 상표
검색으로 실제 등록 상표인지 한 번 더 확인한다. 확인되면 그 단어를 사전에
'브랜드'로 저장해두어, 다음부터는 같은 단어를 다시 조회하지 않는다.
KIPRIS 조회가 실패하거나 키가 없으면 조용히 건너뛰고 기존 방식대로 판정한다.
"""

from app.config.settings import KIPRIS_SERVICE_KEY
from app.core.collector.kipris_client import is_registered_trademark
from app.db import repository

from .text_utils import tokenize

COMPATIBLE_HINT_WORDS = ["호환", "정품", "공식", "라이선스"]


def assess_brand_risk(product_name: str) -> tuple[str, str]:
    """returns (level, basis). level은 '안전' | '주의' | '제외'."""
    tokens = tokenize(product_name)
    brand_hits = [t for t in tokens if repository.lookup_word_type(t) == "브랜드"]
    kipris_checked = False

    if not brand_hits and tokens and KIPRIS_SERVICE_KEY:
        kipris_checked = True
        candidate = tokens[0]
        if repository.lookup_word_type(candidate) is None and is_registered_trademark(candidate):
            repository.promote_word_to_dictionary(candidate, "브랜드")
            brand_hits = [candidate]

    if not brand_hits:
        basis = "브랜드 사전에 매칭되는 단어가 상품명에 없음"
        if kipris_checked:
            basis += " (KIPRIS 상표 조회 포함)"
        return "안전", basis

    brand = brand_hits[0]
    has_compatible_hint = any(hint in product_name for hint in COMPATIBLE_HINT_WORDS)
    if has_compatible_hint:
        return (
            "주의",
            f"브랜드명 '{brand}' 포함 + '호환/정품' 등의 표현 발견 — 실제 정품/호환 여부 사람 확인 필요",
        )
    return "제외", f"등록된 브랜드명 '{brand}'이 상품명에 그대로 포함됨"
