# ==========================================================
# 구매확정 화면 (ui/purchase_confirmed.py)
# ----------------------------------------------------------
# 다른 단계 화면과 같은 형식(엑셀 양식 전체 컬럼 표 + 너비/높이 조절 슬라이더 +
# 클릭 시 상세정보)으로 목록을 보여줍니다.
#
# "수집하기" 버튼은 없습니다: 쿠팡에 '구매확정' 상태를 판매자가 직접 조회/변경하는
# 공식 API가 없기 때문입니다. 대신, 배송완료 화면에서 '수집하기'를 누르면 주문 후
# 30일이 지난 배송완료 주문이 자동으로 이 구매확정 단계로 넘어옵니다
# (services/sync_service.advance_confirmed_orders). 쿠팡은 배송완료 후 일정 기간이
# 지나면 구매확정으로 자동 처리되는데, 원본에 배송완료 날짜가 없어 '주문일'로 판단합니다.
# ==========================================================

import streamlit as st

import models
from repositories import order_repository
from ui import common


def render() -> None:
    st.header("구매확정")
    st.caption(
        "배송완료 화면에서 '수집하기'를 누르면 **주문 후 30일이 지난 배송완료 주문**이 "
        "자동으로 여기(구매확정)로 넘어옵니다. (쿠팡에 구매확정 조회 API가 없어 주문일 기준으로 판단)"
    )

    st.divider()

    orders = order_repository.list_orders_by_work_status(models.WORK_STATUS_PURCHASE_CONFIRMED)

    if not orders:
        st.info("구매확정 주문이 없습니다.")
        return

    keyword = st.text_input("구매확정 목록 검색", placeholder="주문번호, 수령자, 송장번호 등으로 검색")

    # 전화번호·개인통관고유부호는 실제 업무에 매번 필요해서 항상 그대로 보여줍니다.
    reveal = True

    all_rows = [common.build_full_row(order, idx + 1, reveal=reveal) for idx, order in enumerate(orders)]

    filtered_pairs = [(order, row) for order, row in zip(orders, all_rows) if common.matches_search(row, keyword)]

    if not filtered_pairs:
        st.info("검색 결과가 없습니다.")
        return

    filtered_orders = [pair[0] for pair in filtered_pairs]
    filtered_rows = [pair[1] for pair in filtered_pairs]

    # 샵마인식 액션 버튼 바. 구매확정은 확인/처리 주 버튼이 없습니다(유틸 버튼만).
    common.render_shopmine_action_bar("purchase_confirmed", filtered_orders)

    # 행(셀) 클릭 → 오른쪽 상세. 표+상세를 fragment로 그려 스크롤이 위로 안 튐.
    common.render_full_table(
        filtered_rows, filtered_orders, key="purchase_confirmed",
        detail_renderer=lambda o: common.render_full_detail(o, reveal),
        multi_select=True,
    )

    # 표 아래 합계 요약 바(총 건수·결제·수수료·정산) — 신규주문과 동일.
    common.render_order_summary_bar(filtered_orders)
