# ==========================================================
# 전역 주문 검색 화면 (ui/order_search.py)
# ----------------------------------------------------------
# 단계(신규주문/배송중/배송완료/구매확정/반품 등)와 상관없이 전체 주문에서
# 한 번에 찾습니다. 어느 탭에 있는 주문인지 몰라도 바로 찾을 수 있습니다.
# 결과 표의 '주문상태' 컬럼으로 그 주문이 지금 어느 단계인지 보입니다.
# ==========================================================

import streamlit as st

from repositories import order_repository
from ui import common


def render() -> None:
    st.header("전역 주문 검색")
    st.caption(
        "단계(신규주문/배송중/배송완료/구매확정/반품 등)와 상관없이 **전체 주문**에서 찾습니다. "
        "주문번호·구매자/수령자 이름·전화·송장·배대지(GR)·상품명으로 검색됩니다. "
        "결과의 '주문상태' 칸으로 그 주문이 지금 어느 단계인지 알 수 있습니다."
    )

    keyword = st.text_input(
        "검색어", placeholder="주문번호 / 이름 / 전화 / 송장 / GR / 상품명",
        key="order_search_kw",
    )
    if not keyword.strip():
        st.info("검색어를 입력하면 전체 주문에서 찾아 보여줍니다.")
        return

    orders = order_repository.search_orders(keyword)
    if not orders:
        st.info("검색 결과가 없습니다. (다른 검색어로 시도해보세요)")
        return

    st.caption(f"검색 결과 {len(orders)}건 (최대 300건까지 표시)")

    common.warm_product_links(orders)  # 상품링크 배치 선조회(렉 방지)
    rows = [common.build_full_row(order, idx + 1, reveal=True) for idx, order in enumerate(orders)]

    common.render_full_table(
        rows, orders, key="order_search",
        detail_renderer=lambda o: common.render_full_detail(o, True),
        multi_select=False,
    )
