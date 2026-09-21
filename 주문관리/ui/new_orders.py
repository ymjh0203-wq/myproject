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
    # 수집은 백그라운드로 돌아, 도중에 다른 메뉴로 옮겨도 취소되지 않고 끝까지 진행됩니다.
    # 이 화면은 '신규주문(결제완료)'만 최신화합니다. 다른 단계(발송대기/배송중 등)에서
    # 각자 수집을 동시에 눌러도 서로 막지 않습니다. (취소·반품 정리도 함께)
    common.render_background_collect(
        period_from, period_to, key="new_orders",
        stages=[models.WORK_STATUS_NEW], reconcile=True,
    )

    common.render_live_count_banner(models.WORK_STATUS_NEW)

    st.divider()

    # ------------------------------------------------------
    # 수집 결과 목록 + 검색 + 발송대기 이동 + 엑셀파일생성
    # ------------------------------------------------------
    orders = order_repository.list_orders_by_work_status(models.WORK_STATUS_NEW)

    # ④ 수집결과내 검색(필터) — 주문이 없어도 '항상' 보입니다(발송대기처럼).
    keyword = st.text_input(
        "🔎 수집결과내 검색(필터)",
        placeholder="주문번호 · 상품명 · 수령자 · 구매자 등으로 검색",
        key="new_orders_search",
        help="입력하면 아래 표가 실시간으로 걸러집니다. (수집한 결과 안에서 필터)",
    )

    if not orders:
        st.info("신규주문이 없습니다. 위 '수집하기' 버튼을 눌러보세요.")
        return

    # 전화번호·개인통관고유부호는 실제 발송 업무에 매번 필요해서 항상 그대로 보여줍니다.
    reveal = True
    common.warm_product_links(orders)  # 상품링크 배치 선조회(첫 로드 렉 방지)
    all_rows = [common.build_full_row(order, idx + 1, reveal=reveal) for idx, order in enumerate(orders)]

    filtered_pairs = [
        (order, row) for order, row in zip(orders, all_rows) if common.matches_search(row, keyword)
    ]

    if not filtered_pairs:
        st.info("검색 결과가 없습니다.")
        return

    filtered_orders = [pair[0] for pair in filtered_pairs]
    filtered_rows = [pair[1] for pair in filtered_pairs]

    # 샵마인식 액션 버튼 바(공용). 신규주문의 주 버튼은 '주문확인'(발송대기 이동)입니다.
    # 통계·표시항목설정·바꾸기설정·엑셀양식설정·엑셀파일생성은 바 안에서 처리됩니다.
    move_clicked = common.render_shopmine_action_bar(
        "new_orders", filtered_orders,
        primary_label="📦 주문확인", primary_help="선택한 주문을 발송대기로 이동합니다.",
    )

    # 표에 체크박스(부분선택 + 머리글 전체선택). 체크한 행이 1건이면 오른쪽에 상세가 뜹니다.
    # 표+상세를 fragment로 그려, 체크/해제할 때 화면이 위로 튀지 않습니다.
    def _no_detail(selected_order):
        common.render_full_detail(selected_order, reveal)
        if st.button(
            "🚚 이 주문만 발송대기로 이동",
            key=f"no_move_{selected_order['id']}",
            width="stretch",
        ):
            # 상세패널은 fragment 안이라, 공통 헬퍼로 예약 → 메인에서 다이얼로그를 엽니다.
            common.open_dialog_deferred(_confirm_move_to_ready, [selected_order])

    _, selected_orders, _, _ = common.render_full_table(
        filtered_rows, filtered_orders, key="new_orders", detail_renderer=_no_detail, multi_select=True
    )

    # 표 아래 합계 요약 바 (샵마인 하단 상태바처럼) — 지금 목록(검색 반영) 기준.
    common.render_order_summary_bar(filtered_orders)

    if move_clicked:
        if not selected_orders:
            st.warning("이동할 주문을 표 왼쪽 체크박스로 선택해주세요.")
        else:
            _confirm_move_to_ready(selected_orders)
