# ==========================================================
# 긴급/문의관리 화면 (ui/urgent_inquiries.py)
# ----------------------------------------------------------
# 쿠팡에 실제로 확인되는 문의 관련 API가 두 개라서(상품문의 + 콜센터문의),
# 둘을 한 목록("CS문의")으로 합쳐서 보여줍니다. "문의종류" 칸으로 어느
# 쪽에서 온 문의인지 구분합니다.
#   - 상품문의(onlineInquiries): 구매자가 상품 페이지에서 남긴 질문
#   - 콜센터문의(callCenterInquiries): 고객센터로 들어온 CS문의
#     (2026-07-21 기준 이 API 자체가 쿠팡 서버에서 "내부 오류(500)"를 계속
#      응답하고 있습니다. 판매자님 계정에 이 API가 활성화되어 있는지 쿠팡
#      오픈API 담당 쪽에 확인이 필요합니다 - 저희 코드 문제로 보이진 않습니다)
# ==========================================================

import pandas as pd
import streamlit as st

from repositories import inquiry_repository, order_repository, product_link_repository
from services import claims_sync_service, product_link_service
from ui import common, settings


def _render_answer_form(detail: dict) -> None:
    """
    상품문의에 답변을 쓰고 쿠팡에 등록하는 부분입니다.

    "전송"을 누르면 실제로 쿠팡 상품페이지에 공개 답변이 올라갑니다.
    되돌릴 수 없고 같은 문의에 두 번 답변할 수도 없어서, 실수로 눌리지 않도록
    확인 체크박스를 하나 거쳐야 보내집니다.
    """
    row_id = detail.get("_row_id")
    st.markdown("**답변 작성**")

    if detail.get("_answered"):
        st.info(
            "이미 답변이 등록된 문의입니다. 쿠팡은 같은 문의에 두 번 답변할 수 없어서 "
            "추가 답변은 보낼 수 없습니다."
        )
        if detail.get("_answer_content"):
            st.text_area(
                "등록된 답변",
                value=detail["_answer_content"],
                disabled=True,
                key=f"answered_view_{row_id}",
            )
        return

    content = st.text_area(
        "답변 내용",
        key=f"answer_text_{row_id}",
        height=140,
        placeholder="고객에게 보낼 답변을 입력하세요. 상품 페이지에 공개로 올라갑니다.",
    )

    confirmed = st.checkbox(
        "위 내용으로 쿠팡에 공개 답변을 등록합니다 (되돌릴 수 없음)",
        key=f"answer_confirm_{row_id}",
    )

    if st.button("답변 전송", type="primary", key=f"answer_send_{row_id}", disabled=not confirmed):
        with st.spinner("쿠팡에 답변을 등록하는 중입니다..."):
            result = claims_sync_service.answer_product_inquiry(row_id, content)
        if result["succeeded"]:
            st.success(result["message"])
            st.rerun()
        else:
            st.error(result["message"])


def _render_list_with_detail(rows: list, key: str) -> None:
    """
    문의 목록 표를 그리고, 행을 클릭하면 아래에 그 문의의 전체 내용을 항목/값
    형태로 보여줍니다. (표에서는 '내용'이 잘려서 안 보이기 때문에)
    상품문의라면 여기서 바로 답변을 써서 쿠팡에 보낼 수 있습니다.
    """
    # 다른 단계와 같은 AG-Grid 표(왼쪽 체크박스+머리글 전체선택, 컬럼 드래그 순서저장).
    # 밑줄로 시작하는 항목은 내부 처리용이라 표에는 안 보이게 하고, 체크박스가 달리도록
    # 맨 앞에 'No' 번호를 붙입니다. (답변 등록에는 내부 항목이 필요하므로 orders엔 원본을 넘김)
    from ui import aggrid_table

    visible_rows = [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
    grid_rows = []
    for i, vr in enumerate(visible_rows):
        grid_row = {"No": i + 1}
        grid_row.update(vr)
        grid_rows.append(grid_row)
    grid_df = pd.DataFrame(grid_rows) if grid_rows else pd.DataFrame(columns=["No"])

    st.caption(
        "표 왼쪽 체크박스를 체크하면 아래에 문의 전체 내용이 나타납니다. "
        "컬럼(항목)은 마우스로 끌어 순서를 바꿀 수 있고 자동 저장됩니다."
    )
    selected_records, _edited, _clicked = aggrid_table.render_orders_grid(
        grid_df, key=key, orders=rows, height=440
    )
    st.caption(f"총 {len(rows)}건")

    st.divider()
    if selected_records:
        # 체크한 문의(여럿이면 마지막)의 전체 내용을 아래에 보여줍니다.
        detail = selected_records[-1]
        st.markdown("**문의 상세내용**")
        detail_df = pd.DataFrame(
            [(k, str(v)) for k, v in detail.items() if not k.startswith("_")],
            columns=["항목", "값"],
        ).set_index("항목")
        st.table(detail_df)

        if detail.get("문의종류") == "상품문의":
            # 문의온 상품의 쿠팡 상품 페이지를 크롬으로 바로 열 수 있게 합니다.
            _purl = detail.get("_product_url")
            if _purl:
                if st.button("🔗 크롬으로 이 상품 페이지 열기", key=f"inq_prod_open_{detail.get('_row_id')}"):
                    if not common.open_in_chrome(_purl):
                        st.warning("크롬을 찾지 못했습니다. 아래 주소를 복사해 여세요.")
                        st.code(_purl)
                st.caption(f"상품 링크: {_purl}")
            _render_answer_form(detail)
        else:
            st.caption(
                "콜센터문의는 답변 등록 API가 따로 있는데, 조회 API부터 쿠팡 쪽 오류로 "
                "동작하지 않아서 아직 연결하지 않았습니다."
            )

        # 이 문의의 원주문 상세내역(고객·상품·주소 등)도 함께 — 발송대기 상세처럼.
        _moid = detail.get("상품/주문번호")
        if _moid and str(_moid) not in ("", "-"):
            _order = order_repository.get_full_order_by_market_id(_moid)
            if _order:
                st.divider()
                st.markdown("**주문 상세내역**")
                common.render_full_detail(_order, reveal=True)
    else:
        st.caption("표 왼쪽 체크박스를 체크하면 여기에 문의 전체 내용이 나타납니다.")


def _collect_clicked(period_from, period_to) -> None:
    """상품문의 + 콜센터문의를 둘 다 수집합니다. (수집은 항상 '오늘까지'로 새 문의를 놓치지 않게)"""
    from datetime import date
    period_to = date.today()
    period_from = min(period_from, period_to)
    errors = []
    # 콜센터문의는 쿠팡 API가 계속 500 오류를 내서 기본으로 꺼져 있습니다.
    # (설정 > 수집 설정에서 켤 수 있습니다)
    collect_call_center = settings.is_call_center_sync_enabled()
    spinner_text = (
        "쿠팡에서 CS문의(상품문의 + 콜센터문의)를 가져오는 중입니다..."
        if collect_call_center
        else "쿠팡에서 상품문의를 가져오는 중입니다..."
    )
    with st.spinner(spinner_text):
        r1 = claims_sync_service.sync_product_inquiries(period_from, period_to)
        if r1["status"] == "fail":
            errors.append(f"상품문의: {r1['error_message']}")

        r2 = None
        if collect_call_center:
            r2 = claims_sync_service.sync_call_center_inquiries(period_from, period_to)
            if r2["status"] == "fail":
                errors.append(f"콜센터문의: {r2['error_message']}")

    if errors:
        st.error("일부 수집 실패:\n" + "\n".join(f"- {e}" for e in errors))

    succeeded = r1["status"] != "fail" or (r2 is not None and r2["status"] != "fail")
    if succeeded:
        total_new = (r1.get("new_count") or 0) + ((r2 or {}).get("new_count") or 0)
        st.success(f"수집 완료 - 신규 {total_new}건")
        st.rerun()


def _unified_rows() -> list:
    """상품문의 + 콜센터문의를 같은 형식(문의종류 칸 추가)으로 합쳐서 돌려줍니다."""
    rows = []
    for i in inquiry_repository.list_product_inquiries():
        # 쿠팡 문의 응답에는 상품명이 없고 ID만 들어있어서, 이미 수집해둔 주문
        # 데이터에서 그 ID로 상품명/옵션을 찾아 붙여줍니다.
        # 쿠팡 상품조회로 상품명·'정확한 옵션명(vendorItemId 기준)'·링크정보를 확보(캐시).
        # 문의의 vendorItemId로 조회하므로, 주문 매칭이 상품단위여도 옵션을 정확히 알 수 있습니다.
        resolved = product_link_service.resolve_inquiry_product(
            i.get("seller_product_id"), i.get("vendor_item_id"), i.get("market_account_id")
        )
        resolved_option = (resolved or {}).get("option_name") or ""

        product = inquiry_repository.find_product_info(
            i.get("seller_product_id"), i.get("vendor_item_id"), i.get("order_ids")
        )
        if product:
            product_name = product["product_name"] or (resolved or {}).get("product_name") or "-"
            if product["matched_by"] == "옵션" and product.get("option_name"):
                option_name = product["option_name"]  # 주문에서 옵션까지 정확히 매칭됨
            else:
                # 상품 단위 매칭이라 주문상 옵션이 불확실 → 쿠팡 상품조회의 옵션명을 씁니다.
                option_name = resolved_option or "(옵션 확인 불가)"
        elif resolved and resolved.get("product_name"):
            product_name = resolved["product_name"]
            option_name = resolved_option or "-"
        else:
            product_name = "(주문 내역에 없는 상품)"
            option_name = "-"

        # 문의온 상품의 쿠팡 상품 페이지 링크(위 resolve가 productId/itemId를 캐시에 채워둠).
        _vid = i.get("vendor_item_id")
        product_url = ""
        if _vid:
            _cached = product_link_repository.get(_vid)
            product_url = common.coupang_product_url(
                _vid, (_cached or {}).get("product_id"), (_cached or {}).get("item_id")
            )

        rows.append(
            {
                "문의종류": "상품문의",
                "문의번호": i["inquiry_id"],
                "상점": i.get("market_account_name") or "-",
                "상품명": product_name,
                "옵션": option_name,
                "상품/주문번호": i["market_item_id"] or "-",
                "내용": i["content"] or "-",
                "처리상태": "답변완료" if i["answered"] else "미답변",
                "전화번호": "-",
                "문의일시": i["inquiry_at"] or "-",
                # --- 아래 밑줄 항목은 내부 처리용이라 표에는 안 보입니다 ---
                "_needs_action": not i["answered"],
                "_row_id": i["id"],
                "_answered": bool(i["answered"]),
                "_answer_content": i.get("answer_content") or "",
                "_product_url": product_url,
            }
        )
    for i in inquiry_repository.list_call_center_inquiries():
        status = i["inquiry_status"] or "-"
        rows.append(
            {
                "문의종류": "콜센터문의",
                "문의번호": i["inquiry_id"],
                "상점": i.get("market_account_name") or "-",
                # 콜센터문의 응답에는 상품 정보가 없어서 상품문의와 칸만 맞춰둡니다.
                "상품명": "-",
                "옵션": "-",
                "상품/주문번호": i["market_order_id"] or "-",
                "내용": i["content"] or "-",
                "처리상태": status,
                "전화번호": i["buyer_phone"] or "-",
                "문의일시": i["inquiry_at"] or "-",
                "_needs_action": "COMPLETE" not in status.upper(),
                "_row_id": None,
                "_answered": "COMPLETE" in status.upper(),
                "_answer_content": "",
            }
        )
    # 최신 문의가 위로 오도록 문의일시 기준 내림차순 정렬
    rows.sort(key=lambda r: r["문의일시"], reverse=True)
    return rows


def render() -> None:
    st.header("긴급/문의관리")
    st.subheader("CS문의 (상품문의 + 콜센터문의)")
    if settings.is_call_center_sync_enabled():
        st.caption("쿠팡 상품문의와 콜센터문의를 한 곳에 모아 보여줍니다.")
    else:
        st.caption(
            "지금은 상품문의만 수집합니다. 쿠팡 콜센터문의 API가 계속 '내부 오류(500)'를 "
            "응답해서 꺼둔 상태입니다. 쿠팡 오픈API 담당 쪽에서 해결되면 "
            "설정 > 수집 설정에서 다시 켤 수 있습니다."
        )

    period_from, period_to = common.render_period_picker("cs_inq", "문의일(시작)", "문의일(종료)")
    if st.button("수집하기", type="primary", width="stretch", key="cs_inq_collect"):
        _collect_clicked(period_from, period_to)

    rows = _unified_rows()
    # ★'현재 미답변(처리 필요)' 건수 = 누적 전체가 아니라 지금 답변해야 할 문의 수.
    pending_cnt = sum(1 for r in rows if r.get("_needs_action"))
    p_at = claims_sync_service.get_last_sync_at("product_inquiry")
    c_at = claims_sync_service.get_last_sync_at("call_center_inquiry")
    if p_at or c_at:
        st.info(f"**현재 미답변 CS문의: {pending_cnt}건** (마지막 확인: {p_at or c_at})")
    else:
        st.caption("아직 한 번도 수집하지 않았습니다.")

    st.divider()

    if not rows:
        st.info("CS문의 내역이 없습니다. 위 '수집하기'를 눌러보세요.")
        return

    type_options = ["전체", "상품문의", "콜센터문의"]
    store_options = ["전체"] + sorted({r["상점"] for r in rows})

    col_search, col_type, col_store, col_action = st.columns([2, 1, 1, 1])
    with col_search:
        keyword = st.text_input("CS문의 검색", placeholder="문의번호, 주문/상품번호, 내용 등으로 검색", key="cs_inq_search")
    with col_type:
        type_filter = st.selectbox("문의종류", options=type_options, key="cs_inq_type_filter")
    with col_store:
        store_filter = st.selectbox("상점", options=store_options, key="cs_inq_store_filter")
    with col_action:
        st.write("")
        action_only = st.checkbox("처리 필요한 것만 보기", value=True, key="cs_inq_action_only")

    filtered = []
    for row in rows:
        if action_only and not row["_needs_action"]:
            continue
        if type_filter != "전체" and row["문의종류"] != type_filter:
            continue
        if store_filter != "전체" and row["상점"] != store_filter:
            continue
        # 검색은 표에 보이는 항목으로만(밑줄로 시작하는 내부용 항목 제외) 합니다.
        visible = {k: v for k, v in row.items() if not k.startswith("_")}
        if not common.matches_search(visible, keyword):
            continue
        # 답변 등록에 필요한 내부 항목까지 그대로 넘깁니다(표에는 안 보입니다).
        filtered.append(row)

    if not filtered:
        st.info("조건에 맞는 CS문의가 없습니다.")
        return

    st.caption("행을 클릭하면 아래에 전체 내용이 나타납니다.")
    _render_list_with_detail(filtered, key="cs_inq")
