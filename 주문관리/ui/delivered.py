# ==========================================================
# 배송완료 화면 (ui/delivered.py)
# ----------------------------------------------------------
# "수집하기"를 누르면 쿠팡에서 배송완료(FINAL_DELIVERY) 상태인 주문을
# 직접 가져옵니다.
#
# 목록 표(엑셀 양식 전체 컬럼 + 너비/높이 조절 슬라이더 + 클릭 시 상세정보)는
# ui/common.py의 render_full_table/render_full_detail을 공용으로 씁니다.
#
# TODO: "구매확정"으로의 전환은 보통 구매자가 직접 누르거나 일정 기간 후
# 자동으로 처리되는 것으로 보입니다. 판매자가 호출하는 API가 있는지 아직
# 확인하지 못해서, 이 화면에는 상태 이동 버튼을 만들지 않았습니다.
# ==========================================================

import streamlit as st

import models
from repositories import order_repository
from services import sync_service
from ui import common


def render() -> None:
    st.header("배송완료")

    period_from, period_to = common.render_period_picker("delivered", "결제일시(시작)", "결제일시(종료)")
    st.caption(
        "※ 위 기간은 '수집하기'로 쿠팡에서 **가져올 범위**에만 적용됩니다. 아래 목록은 "
        "현재 배송완료 상태인 주문을 **기간과 무관하게 전부** 보여줍니다."
    )
    # 수집은 백그라운드로 돌아, 도중에 다른 메뉴로 옮겨도 취소되지 않고 끝까지 진행됩니다.
    # advance_confirmed=True: 주문 후 30일 넘은 배송완료 주문을 구매확정으로 자동 전진시켜,
    # 배송완료 화면에 오래된 완료 주문이 계속 쌓이는 것을 막습니다.
    common.render_background_collect(
        period_from, period_to, key="delivered", stages=[models.WORK_STATUS_DELIVERED],
        advance_confirmed=True,
    )

    common.render_live_count_banner(models.WORK_STATUS_DELIVERED)

    st.divider()

    orders = order_repository.list_orders_by_work_status(models.WORK_STATUS_DELIVERED)

    if not orders:
        st.info("배송완료 주문이 없습니다. 위 '수집하기'를 눌러보세요.")
        return

    keyword = st.text_input("배송완료 목록 검색", placeholder="주문번호, 수령자, 송장번호 등으로 검색")

    # 전화번호·개인통관고유부호는 실제 업무에 매번 필요해서 항상 그대로 보여줍니다.
    reveal = True

    all_rows = [common.build_full_row(order, idx + 1, reveal=reveal) for idx, order in enumerate(orders)]

    filtered_pairs = [(order, row) for order, row in zip(orders, all_rows) if common.matches_search(row, keyword)]

    if not filtered_pairs:
        st.info("검색 결과가 없습니다.")
        return

    filtered_orders = [pair[0] for pair in filtered_pairs]
    filtered_rows = [pair[1] for pair in filtered_pairs]

    # 샵마인식 액션 버튼 바. 배송완료는 확인/처리 주 버튼이 없습니다(유틸 버튼만).
    common.render_shopmine_action_bar("delivered", filtered_orders)

    # 행(셀) 클릭 → 오른쪽 상세. 표+상세를 fragment로 그려 스크롤이 위로 안 튐.
    common.render_full_table(
        filtered_rows, filtered_orders, key="delivered",
        detail_renderer=lambda o: common.render_full_detail(o, reveal),
        multi_select=True,
    )

    # 표 아래 합계 요약 바(총 건수·결제·수수료·정산) — 신규주문과 동일.
    common.render_order_summary_bar(filtered_orders)
