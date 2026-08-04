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

from repositories import inquiry_repository
from services import claims_sync_service
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
    # 밑줄로 시작하는 항목은 내부 처리용이라 표에는 안 보이게 합니다.
    visible_rows = [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
    df = pd.DataFrame(visible_rows)
    table_height = max(100, min(common.TABLE_ROW_HEIGHT * (len(rows) + 1) + 3, 600))
    event = st.dataframe(
        df,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"{key}_table",
        height=table_height,
        row_height=common.TABLE_ROW_HEIGHT,
        width="stretch",
    )
    st.caption(f"총 {len(rows)}건")

    selected = event.selection.rows
    # 답변을 보내거나 필터가 바뀌면 목록이 줄어드는데, 표에는 조금 전 클릭한 행
    # 번호가 남아있을 수 있습니다. 그 번호가 지금 목록 범위를 벗어나면 무시합니다.
    # (안 그러면 rows[없는 번호]에서 오류가 납니다)
    if len(selected) == 1 and 0 <= selected[0] < len(rows):
        detail = rows[selected[0]]
        st.markdown("**문의 상세내용** (행을 다시 클릭하면 닫힙니다)")
        detail_df = pd.DataFrame(
            [(k, str(v)) for k, v in detail.items() if not k.startswith("_")],
            columns=["항목", "값"],
        ).set_index("항목")
        st.table(detail_df)

        if detail.get("문의종류") == "상품문의":
            _render_answer_form(detail)
        else:
            st.caption(
                "콜센터문의는 답변 등록 API가 따로 있는데, 조회 API부터 쿠팡 쪽 오류로 "
                "동작하지 않아서 아직 연결하지 않았습니다."
            )


def _collect_clicked(period_from, period_to) -> None:
    """상품문의 + 콜센터문의를 둘 다 수집합니다."""
    if period_from > period_to:
        st.error("시작일이 종료일보다 늦을 수 없습니다.")
        return
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
        product = inquiry_repository.find_product_info(
            i.get("seller_product_id"), i.get("vendor_item_id"), i.get("order_ids")
        )
        if product:
            product_name = product["product_name"] or "-"
            option_name = product["option_name"] or "-"
            if product["matched_by"] == "상품":
                # 상품 단위로만 맞춘 경우 옵션은 다른 옵션일 수 있어 표시하지 않습니다.
                option_name = "(옵션 확인 불가)"
        else:
            product_name = "(주문 내역에 없는 상품)"
            option_name = "-"

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

    p_at = claims_sync_service.get_last_sync_at("product_inquiry")
    p_cnt = claims_sync_service.get_last_fetched_count("product_inquiry")
    c_at = claims_sync_service.get_last_sync_at("call_center_inquiry")
    c_cnt = claims_sync_service.get_last_fetched_count("call_center_inquiry")
    if settings.is_call_center_sync_enabled():
        if p_at or c_at:
            st.info(
                f"**쿠팡 기준 현재 상품문의 {p_cnt if p_cnt is not None else '-'}건 / "
                f"콜센터문의 {c_cnt if c_cnt is not None else '-'}건** "
                f"(마지막 확인: {p_at or c_at})"
            )
        else:
            st.caption("아직 한 번도 수집하지 않았습니다.")
    elif p_at:
        st.info(
            f"**쿠팡 기준 현재 상품문의 {p_cnt if p_cnt is not None else '-'}건** "
            f"(마지막 확인: {p_at})"
        )
    else:
        st.caption("아직 한 번도 수집하지 않았습니다.")

    st.divider()

    rows = _unified_rows()
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
