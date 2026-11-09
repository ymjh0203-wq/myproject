"""WING 상품 등록 전에 준비할 안전한 기본값을 구성한다."""

from __future__ import annotations

from collections.abc import Sequence
import re

from .detail_translation import TRANSLATION_MODE_LABELS


DEFAULT_STOCK_QUANTITY = 999
DEFAULT_SHIPPING_LEAD_DAYS = 9
DEFAULT_NOTICE_CATEGORY = "기타 재화"
DEFAULT_NOTICE_DETAIL = "상세페이지 참조"
DEFAULT_DETAIL_ENTRY_MODE = "editor"
RETURN_FEE_PRIMARY_CAP = 200_000
RETURN_FEE_FALLBACK_CAP = 100_000
RETURN_FEE_WING_GLOBAL_MINIMUM = 0
RETURN_FEE_WING_GLOBAL_MAXIMUM = 250_000
RETURN_FEE_LOW_PRICE_THRESHOLD = 100_000
RETURN_FEE_PRICE_BUFFER = 1
RETURN_FEE_INPUT_UNIT = 10_000
RETURN_FEE_LOW_PRICE_ROUNDING_UNIT = 10_000
# WING은 전체 허용범위 안에서도 상품·배송조건에 따라 더 낮은 상한을
# 검증 팝업으로 돌려준다. 프로그램은 알려진 금액 단계를 데이터로 보관하고,
# 실제 화면/팝업의 상한이 있으면 아래 기본값보다 그 값을 우선한다.
RETURN_FEE_KNOWN_WING_MAXIMUMS = (
    250_000,
    200_000,
    150_000,
    100_000,
    80_000,
    60_000,
    50_000,
    40_000,
    30_000,
    20_000,
    10_000,
    0,
)
SHIPPING_FEE_LABELS = {
    "free": "무료배송",
    "cash_on_delivery": "착불",
}
DETAIL_ENTRY_MODE_LABELS = {
    "editor": "에디터 작성",
}


def _clean_list(values: Sequence[str] | None) -> list[str]:
    """빈 값과 중복을 제거하되 사용자가 지정한 순서는 유지한다."""

    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        item = str(value).strip()
        if item and item not in seen:
            cleaned.append(item)
            seen.add(item)
    return cleaned


def build_return_shipping_fee_policy(minimum_selling_price: int) -> dict:
    """반품배송비(편도) 입력 순서와 현재 상품의 목표 금액을 반환한다."""

    if minimum_selling_price <= 0:
        attempts: list[int] = []
        target = None
        reason = "minimum_selling_price_required"
    elif minimum_selling_price < RETURN_FEE_LOW_PRICE_THRESHOLD:
        # 최저 판매가보다 낮은 금액 중 가장 큰 만원 단위를 사용한다.
        # 36,160원 -> 30,000원, 19,150원 -> 10,000원이며,
        # 판매가가 정확히 만원 단위여도 반드시 한 단계 아래로 내려간다.
        target = maximum_return_fee_below(
            minimum_selling_price,
            unit=RETURN_FEE_LOW_PRICE_ROUNDING_UNIT,
        )
        attempts = [target]
        reason = "largest_ten_thousand_below_minimum_price"
    else:
        attempts = [RETURN_FEE_PRIMARY_CAP, RETURN_FEE_FALLBACK_CAP]
        target = attempts[0]
        reason = "try_primary_then_fallback"

    return {
        "field": "return_shipping_fee_one_way",
        "field_label": "반품배송비(편도)",
        "target": target,
        "attempts_in_order": attempts,
        "primary_cap": RETURN_FEE_PRIMARY_CAP,
        "fallback_cap": RETURN_FEE_FALLBACK_CAP,
        "wing_global_minimum": RETURN_FEE_WING_GLOBAL_MINIMUM,
        "wing_global_maximum": RETURN_FEE_WING_GLOBAL_MAXIMUM,
        "known_wing_maximums": list(RETURN_FEE_KNOWN_WING_MAXIMUMS),
        "low_price_threshold": RETURN_FEE_LOW_PRICE_THRESHOLD,
        "price_buffer": RETURN_FEE_PRICE_BUFFER,
        "input_unit": RETURN_FEE_INPUT_UNIT,
        "low_price_rounding_unit": RETURN_FEE_LOW_PRICE_ROUNDING_UNIT,
        "reason": reason,
        "rejection_strategy": "maximum_below_wing_upper_bound",
    }


def maximum_return_fee_below(upper_bound: int, *, unit: int = RETURN_FEE_INPUT_UNIT) -> int:
    """상한 미만으로 입력할 수 있는 단위 기준 최대 금액을 반환한다."""

    if upper_bound <= 0 or unit <= 0:
        return 0
    return max(((upper_bound - 1) // unit) * unit, 0)


def maximum_return_fee_at_or_below(
    upper_bound: int,
    *,
    unit: int = RETURN_FEE_INPUT_UNIT,
) -> int:
    """허용 최댓값을 넘지 않는 단위 기준 최대 금액을 반환한다."""

    if upper_bound <= 0 or unit <= 0:
        return 0
    return max((upper_bound // unit) * unit, 0)


def extract_return_fee_upper_bound(message: str) -> int | None:
    """WING 오류 문구에서 ``○○원보다 낮게/미만`` 형태의 상한을 찾는다."""

    text = " ".join((message or "").split())
    patterns = (
        r"([0-9][0-9,]*)\s*원?\s*(?:보다\s*낮|미만)",
        r"(?:최대|상한)\s*([0-9][0-9,]*)\s*원",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def extract_return_fee_allowed_maximum(message: str) -> int | None:
    """WING의 ``0원 ~ ○○원 까지 가능합니다`` 안내에서 허용 최댓값을 읽는다."""

    text = " ".join((message or "").split())
    match = re.search(
        r"~\s*([0-9][0-9,]*)\s*원?\s*까지(?:만)?(?:\s*입력)?(?:이)?\s*가능",
        text,
    )
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def recalculate_return_shipping_fee(
    *,
    minimum_selling_price: int,
    wing_validation_message: str,
) -> dict:
    """WING 거부 사유를 반영해 다음 반품배송비(편도)를 계산한다."""

    base_policy = build_return_shipping_fee_policy(minimum_selling_price)
    explicit_upper_bound = extract_return_fee_upper_bound(wing_validation_message)
    message = (wing_validation_message or "").strip()

    if explicit_upper_bound is not None:
        target = maximum_return_fee_below(explicit_upper_bound)
        reason = "maximum_below_explicit_wing_upper_bound"
    elif "판매가" in message and ("낮" in message or "미만" in message):
        target = maximum_return_fee_below(minimum_selling_price)
        explicit_upper_bound = minimum_selling_price
        reason = "maximum_below_minimum_selling_price"
    else:
        target = RETURN_FEE_FALLBACK_CAP
        reason = "wing_rejected_primary_use_fallback_cap"

    return {
        **base_policy,
        "target": target,
        "wing_upper_bound": explicit_upper_bound,
        "reason": reason,
        "source_validation_message": message,
    }


def build_registration_plan(
    *,
    catalog_match_price: int,
    shipping_fee_type: str | None,
    country_of_origin: str,
    stock_quantity: int = DEFAULT_STOCK_QUANTITY,
    shipping_lead_days: int = DEFAULT_SHIPPING_LEAD_DAYS,
    notice_category: str = DEFAULT_NOTICE_CATEGORY,
    notice_detail: str = DEFAULT_NOTICE_DETAIL,
    kc_purchase_agent_checked: bool = True,
    detail_entry_mode: str = DEFAULT_DETAIL_ENTRY_MODE,
    remove_other_seller_sections: bool = True,
    fixed_page_top_assets: Sequence[str] | None = None,
    fixed_page_bottom_assets: Sequence[str] | None = None,
    own_seller_markers: Sequence[str] | None = ("미노바",),
    detail_preview_required: bool = True,
    source_detail_assets: Sequence[str] | None = None,
    translated_detail_assets: Sequence[str] | None = None,
    source_option_assets: Sequence[str] | None = None,
    watermarked_option_assets: Sequence[str] | None = None,
    watermark_enabled: bool = False,
    translation_mode: str = "auto_ko",
    translation_status: str = "not_checked",
    translation_review_approved: bool = False,
) -> dict:
    """상품별 입력값과 누락값을 함께 반환한다.

    원산지는 실제 제조국이어야 하므로 ``상세페이지 참조``로 대체하지 않는다.
    최종 상품등록은 이 계획의 범위 밖이며 항상 사용자가 직접 확인한다.
    """

    origin = (country_of_origin or "").strip()
    fixed_top_assets = _clean_list(fixed_page_top_assets)
    fixed_bottom_assets = _clean_list(fixed_page_bottom_assets)
    seller_markers = _clean_list(own_seller_markers)
    source_assets = _clean_list(source_detail_assets)
    translated_assets = _clean_list(translated_detail_assets)
    option_assets = _clean_list(source_option_assets)
    watermarked_options = _clean_list(watermarked_option_assets)
    missing_fields: list[str] = []
    if catalog_match_price <= 0:
        missing_fields.append("카탈로그 매칭 판매가")
    if shipping_fee_type not in SHIPPING_FEE_LABELS:
        missing_fields.append("배송비 방식")
    if not origin or origin == DEFAULT_NOTICE_DETAIL:
        missing_fields.append("실제 원산지")
    if not kc_purchase_agent_checked:
        missing_fields.append("KC 구매대행 상품 확인")
    if detail_entry_mode not in DETAIL_ENTRY_MODE_LABELS:
        missing_fields.append("상세페이지 입력 방식(에디터 작성)")
    if not remove_other_seller_sections:
        missing_fields.append("다른 판매자 고정영역 제거")
    if not fixed_top_assets:
        missing_fields.append("상단 고정페이지 이미지")
    if not fixed_bottom_assets:
        missing_fields.append("하단 고정페이지 이미지")
    if not seller_markers:
        missing_fields.append("내 판매자 식별어")
    if translation_mode not in TRANSLATION_MODE_LABELS:
        missing_fields.append("상세이미지 번역 방식")
    if translated_assets and not translation_review_approved:
        missing_fields.append("한국어 이미지 검수 승인")

    use_translated_assets = bool(
        translation_mode == "auto_ko"
        and translated_assets
        and translation_review_approved
    )
    effective_product_assets = (
        translated_assets if use_translated_assets else source_assets
    )

    return {
        "price_policy": "catalog_match_price",
        "catalog_match_price": catalog_match_price,
        "selling_price": catalog_match_price if catalog_match_price > 0 else None,
        "stock_quantity": stock_quantity,
        "shipping_lead_days": shipping_lead_days,
        "shipping_fee_type": shipping_fee_type,
        "shipping_fee_label": SHIPPING_FEE_LABELS.get(shipping_fee_type),
        "return_shipping_fee": build_return_shipping_fee_policy(
            catalog_match_price
        ),
        "country_of_origin": origin,
        "product_notice": {
            "category": notice_category,
            "detail": notice_detail,
        },
        "kc_certification": {
            "purchase_agent_product": kc_purchase_agent_checked,
            "label": "구매대행 상품",
        },
        "product_images": {
            "main_image_watermark": False,
            "source_option_assets": option_assets,
            "watermarked_option_assets": watermarked_options,
            "effective_option_assets": (
                watermarked_options
                if watermark_enabled and watermarked_options
                else option_assets
            ),
            "watermark_enabled_for_options": bool(watermark_enabled),
        },
        "detail_page": {
            "entry_mode": detail_entry_mode,
            "entry_mode_label": DETAIL_ENTRY_MODE_LABELS.get(detail_entry_mode),
            "source_cleanup": {
                "remove_other_seller_sections": remove_other_seller_sections,
                "detect_markers": [
                    "판매자 상호/스토어명",
                    "전화번호/고객센터",
                    "카카오톡/위챗/QQ",
                    "QR 코드",
                    "자체 배송/교환/반품 안내",
                ],
                "preserve_sections": [
                    "상품 특징",
                    "규격/치수",
                    "옵션/구성",
                    "사용법/주의사항",
                ],
                "own_seller_markers": seller_markers,
                "low_confidence_action": "keep_for_preview",
            },
            "fixed_page": {
                "top_assets": fixed_top_assets,
                "bottom_assets": fixed_bottom_assets,
                "editor_order": "top_source_bottom",
                "editor_order_label": "상단 → 상품 설명 → 하단",
            },
            "localization": {
                "mode": translation_mode,
                "mode_label": TRANSLATION_MODE_LABELS.get(translation_mode),
                "status": translation_status,
                "source_assets": source_assets,
                "translated_assets": translated_assets,
                "review_approved": translation_review_approved,
                "using_translated_assets": use_translated_assets,
                "effective_product_assets": effective_product_assets,
                "rules": {
                    "detect_language_per_image": True,
                    "translate_chinese_to_korean": True,
                    "rewrite_awkward_korean": True,
                    "preserve_natural_korean": True,
                    "keep_no_text_images_unchanged": True,
                    "preserve_product_photos": True,
                    "preserve_numbers_units_models": True,
                    "low_confidence_action": "save_as_draft",
                },
            },
            "preview_required": detail_preview_required,
        },
        "missing_fields": missing_fields,
        "ready_for_manual_wing_entry": not missing_fields,
        "final_submit_requires_user_confirmation": True,
    }
