"""상세페이지 블록을 보수적으로 정제하고 WING 에디터 순서를 만든다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


SELLER_MARKERS = (
    "판매자",
    "판매처",
    "상호",
    "스토어",
    "상점",
    "shop",
    "seller",
)
CONTACT_MARKERS = (
    "전화번호",
    "문의전화",
    "고객센터",
    "카카오톡",
    "카톡",
    "kakao",
    "위챗",
    "wechat",
    "微信",
    "qq",
    "qr",
)
POLICY_MARKERS = (
    "배송안내",
    "배송 안내",
    "교환안내",
    "교환 안내",
    "반품안내",
    "반품 안내",
    "환불안내",
    "환불 안내",
)
PRODUCT_MARKERS = (
    "상품 특징",
    "제품 특징",
    "사이즈",
    "크기",
    "치수",
    "규격",
    "재질",
    "옵션",
    "구성",
    "사용법",
    "설치",
    "주의사항",
    "제품 사양",
)


def _normalized(text: str) -> str:
    return " ".join((text or "").casefold().split())


def _hits(text: str, markers: Sequence[str]) -> list[str]:
    return [marker for marker in markers if marker.casefold() in text]


def classify_detail_block(
    text: str,
    *,
    own_seller_markers: Sequence[str] = ("미노바",),
) -> dict:
    """OCR/대체텍스트를 기준으로 블록의 처리 방법을 정한다.

    판매자 정보라는 근거가 두 종류 이상 겹칠 때만 자동 제거한다. 제품 정보가
    섞인 블록과 근거가 하나뿐인 블록은 삭제하지 않고 미리보기 대상으로 남긴다.
    """

    normalized = _normalized(text)
    own_hits = _hits(normalized, own_seller_markers)
    seller_hits = _hits(normalized, SELLER_MARKERS)
    contact_hits = _hits(normalized, CONTACT_MARKERS)
    policy_hits = _hits(normalized, POLICY_MARKERS)
    product_hits = _hits(normalized, PRODUCT_MARKERS)

    if own_hits:
        action = "keep"
        reason = "own_fixed_page"
    else:
        evidence_groups = sum(
            bool(group) for group in (seller_hits, contact_hits, policy_hits)
        )
        if evidence_groups >= 2 and not product_hits:
            action = "remove"
            reason = "other_seller_fixed_page"
        elif seller_hits or contact_hits or policy_hits:
            action = "review"
            reason = "seller_marker_mixed_or_uncertain"
        else:
            action = "keep"
            reason = "product_content"

    return {
        "action": action,
        "reason": reason,
        "evidence": {
            "own_seller": own_hits,
            "seller": seller_hits,
            "contact": contact_hits,
            "policy": policy_hits,
            "product": product_hits,
        },
    }


def build_editor_content(
    blocks: Sequence[Mapping[str, object]],
    *,
    fixed_page_top_assets: Sequence[str],
    fixed_page_bottom_assets: Sequence[str],
    own_seller_markers: Sequence[str] = ("미노바",),
) -> dict:
    """정제 결과와 WING 에디터에 넣을 이미지 순서를 반환한다."""

    kept: list[dict] = []
    removed: list[dict] = []
    review: list[dict] = []
    for block in blocks:
        item = dict(block)
        decision = classify_detail_block(
            str(item.get("text") or ""),
            own_seller_markers=own_seller_markers,
        )
        item["decision"] = decision
        if decision["action"] == "remove":
            removed.append(item)
            continue
        kept.append(item)
        if decision["action"] == "review":
            review.append(item)

    fixed_top_items = [
        {"kind": "fixed_page_top", "asset": str(asset).strip()}
        for asset in fixed_page_top_assets
        if str(asset).strip()
    ]
    fixed_bottom_items = [
        {"kind": "fixed_page_bottom", "asset": str(asset).strip()}
        for asset in fixed_page_bottom_assets
        if str(asset).strip()
    ]
    source_items = [
        {"kind": "source", "asset": item.get("asset"), "block": item}
        for item in kept
    ]

    editor_sequence = fixed_top_items + source_items + fixed_bottom_items

    return {
        "kept_blocks": kept,
        "removed_blocks": removed,
        "review_blocks": review,
        "editor_sequence": editor_sequence,
        "preview_required": bool(review),
    }
