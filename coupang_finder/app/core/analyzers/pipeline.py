"""필터 파이프라인 — 브랜드 → 효자상품 → 옵션 → 해외구매대행 순서로 판정하고
결과를 products 테이블에 반영한다.

하나라도 '제외' 조건이면 status='제외' + exclusion_reason 저장, 아니면
status='검토중'(WING 검색수 확인은 Phase 4)으로 넘어간다.
"""

from app.db import repository

from .brand_risk import assess_brand_risk
from .hyoja_detector import assess_hyoja
from .option_quality import assess_option_quality
from .overseas_sourcing import assess_overseas_sourcing


def run_filters(product_id: int) -> dict:
    product = repository.get_product(product_id)
    if product is None:
        raise ValueError(f"상품을 찾을 수 없습니다: id={product_id}")

    threshold = int(repository.get_setting("hyoja_auto_exclude_repeat", "5"))

    brand_level, brand_basis = assess_brand_risk(product["product_name"])
    hyoja_status, hyoja_basis, hyoja_prefix, core_keyword = assess_hyoja(
        product["product_name"], product["seller_name"], threshold
    )
    # Phase 3 시점에는 옵션 데이터를 아직 확보하지 못하므로 '확인 필요'로 고정된다.
    option_quality, option_basis = assess_option_quality(None)
    overseas_score, overseas_basis = assess_overseas_sourcing(product["product_name"])

    status = "검토중"
    exclusion_reason = None
    if brand_level == "제외":
        status, exclusion_reason = "제외", "브랜드 위험"
    elif hyoja_status == "제외":
        status, exclusion_reason = "제외", "효자상품"
    elif option_quality == "번잡":
        status, exclusion_reason = "제외", "옵션 번잡"

    repository.update_product_filters(
        product_id,
        core_keyword=core_keyword,
        brand_risk_level=brand_level,
        brand_risk_basis=brand_basis,
        hyoja_status=hyoja_status,
        hyoja_prefix=hyoja_prefix,
        option_quality=option_quality,
        overseas_score=overseas_score,
        overseas_basis=overseas_basis,
        status=status,
        exclusion_reason=exclusion_reason,
    )

    return {
        "brand_risk_level": brand_level,
        "brand_risk_basis": brand_basis,
        "hyoja_status": hyoja_status,
        "hyoja_basis": hyoja_basis,
        "option_quality": option_quality,
        "overseas_score": overseas_score,
        "status": status,
        "exclusion_reason": exclusion_reason,
    }
