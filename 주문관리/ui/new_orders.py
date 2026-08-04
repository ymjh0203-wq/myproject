# ==========================================================
# 신규주문 화면 (ui/new_orders.py)
# ----------------------------------------------------------
# "수집하기" 버튼을 누르면 쿠팡(지금은 Mock 가상 데이터)에서
# 지정한 결제일시 기간의 신규 주문을 가져와 저장하고, 아래에 목록으로 보여줍니다.
#
# 목록 표(엑셀 양식 전체 컬럼 + 너비/높이 조절 슬라이더 + 클릭 시 상세정보)는
# ui/common.py의 render_full_table/render_full_detail을 공용으로 씁니다.
# 여러 행을 선택하면 '선택 주문 발송대기로 이동'을 쓸 수 있습니다.
#
# 샵마인 화면 구성을 참고해서 만들었지만, 아래 기능들은 아직 뒷단(데이터/로직)이
# 없어서 이번 단계에서는 넣지 않았습니다.
#   - CS / 퀵스타 / 보류처리 / 작업상태지정 : CS 메모관리 기능이 아직 없음
#   - 판매취소 / 지연예고                   : 취소주문 관리 기능이 아직 없음
# ==========================================================

import streamlit as st

import models
from repositories import order_repository
from services import sync_service
from ui import common


@st.dialog("발송대기로 이동")
def _confirm_move_to_ready(orders: list) -> None:
    """
    '선택 주문 발송대기로 이동' 버튼을 눌렀을 때, 실제로 이동하기 전에 한 번 더
    확인하는 창입니다. 실제로는 쿠팡에 "상품준비중" 처리를 요청하고, 성공한
    주문만 우리 프로그램에서도 발송대기로 이동합니다.
    """
    st.write(f"선택한 **{len(orders)}건**을 쿠팡에 발송 준비 처리하고, 발송대기로 이동하시겠습니까?")
    st.caption("쿠팡에 실제로 '상품준비중' 상태 변경을 요청합니다.")

    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("이동", type="primary", width="stretch"):
            with st.spinner("쿠팡에 처리를 요청하는 중입니다..."):
                result = sync_service.move_orders_to_ready_to_ship(orders)
            st.session_state["new_orders_move_result"] = result
            st.rerun()
    with col_no:
        if st.button("취소", width="stretch"):
            st.rerun()


def render() -> None:
    st.header("신규주문")

    if st.session_state.get("new_orders_move_result"):
        result = st.session_state.pop("new_orders_move_result")
        if result["moved_count"]:
            st.success(f"{result['moved_count']}건을 발송대기로 이동했습니다.")
        if result["failed"]:
            failure_lines = "\n".join(
                f"- {item['market_order_id']}: {item['message']}" for item in result["failed"]
            )
            st.error(f"{len(result['failed'])}건은 쿠팡 처리에 실패해서 이동되지 않았습니다.\n{failure_lines}")

    # ------------------------------------------------------
    # 상단: 쇼핑몰 + 결제일시 기간(빠른 선택 포함) + 수집하기
    # ------------------------------------------------------
    st.selectbox("쇼핑몰계정그룹", ["쿠팡"], disabled=True, help="지금은 쿠팡만 연동되어 있습니다. 다른 마켓은 추후 추가 예정입니다.")
    period_from, period_to = common.render_period_picker("new_orders", "결제일시(시작)", "결제일시(종료)")
    collect_clicked = st.button("수집하기", type="primary", width="stretch")

    if collect_clicked:
        if period_from > period_to:
            st.error("시작일이 종료일보다 늦을 수 없습니다.")
        else:
            # 신규주문만이 아니라 전 단계를 함께 최신화합니다. 그래야 이미 발송/배송으로
            # 넘어간 주문이 예전 단계에 그대로 남지 않습니다.
            result = common.collect_all_stages_with_progress(period_from, period_to)
            if result["status"] == "fail":
                st.error(f"주문 수집 실패: {result['error_message']}")
            else:
                closed = result.get("closed_count") or 0
                message = (
                    f"수집 완료 - 전체 단계 최신화 "
                    f"(신규 {result['new_count']}건, 갱신 {result['updated_count']}건"
                    + (f", 취소·반품 정리 {closed}건" if closed else "")
                    + ")"
                )
                if result["error_message"]:
                    st.warning(message + f"\n일부 오류: {result['error_message']}")
                else:
                    st.success(message)
                st.rerun()

    common.render_live_count_banner(models.WORK_STATUS_NEW)

    st.divider()

    # ------------------------------------------------------
    # 수집 결과 목록 + 검색 + 발송대기 이동 + 엑셀파일생성
    # ------------------------------------------------------
    orders = order_repository.list_orders_by_work_status(models.WORK_STATUS_NEW)

    if not orders:
        st.info("신규주문이 없습니다. 위 '수집하기' 버튼을 눌러보세요.")
        return

    col_search, col_move = st.columns([2, 1])
    with col_search:
        keyword = st.text_input("수집결과내 검색", placeholder="주문번호, 상품명, 수령자 등으로 검색")
    with col_move:
        st.write("")
        move_clicked = st.button(
            "선택 주문 발송대기로 이동",
            width="stretch",
            help="표 왼쪽 체크박스로 선택한 주문들을 발송대기로 이동합니다. (머리글 체크박스로 전체선택)",
        )

    # 전화번호·개인통관고유부호는 실제 발송 업무에 매번 필요해서 항상 그대로 보여줍니다.
    reveal = True
    all_rows = [common.build_full_row(order, idx + 1, reveal=reveal) for idx, order in enumerate(orders)]

    excel_rows = [common.build_full_row(order, idx + 1, reveal=True) for idx, order in enumerate(orders)]
    excel_bytes = common.build_excel_bytes(excel_rows)
    st.download_button(
        "엑셀파일생성",
        data=excel_bytes,
        file_name="신규주문.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="new_orders_excel_download",
    )

    filtered_pairs = [
        (order, row) for order, row in zip(orders, all_rows) if common.matches_search(row, keyword)
    ]

    if not filtered_pairs:
        st.info("검색 결과가 없습니다.")
        return

    filtered_orders = [pair[0] for pair in filtered_pairs]
    filtered_rows = [pair[1] for pair in filtered_pairs]

    # 표에 체크박스(부분선택 + 머리글 전체선택). 체크한 행이 1건이면 오른쪽에 상세가 뜹니다.
    # 표+상세를 fragment로 그려, 체크/해제할 때 화면이 위로 튀지 않습니다.
    def _no_detail(selected_order):
        common.render_full_detail(selected_order, reveal)
        if st.button(
            "🚚 이 주문만 발송대기로 이동",
            key=f"no_move_{selected_order['id']}",
            width="stretch",
        ):
            _confirm_move_to_ready([selected_order])

    _, selected_orders, _, _ = common.render_full_table(
        filtered_rows, filtered_orders, key="new_orders", detail_renderer=_no_detail, multi_select=True
    )

    if move_clicked:
        if not selected_orders:
            st.warning("이동할 주문을 표 왼쪽 체크박스로 선택해주세요.")
        else:
            _confirm_move_to_ready(selected_orders)
