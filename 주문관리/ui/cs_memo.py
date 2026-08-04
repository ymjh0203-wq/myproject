# ==========================================================
# CS메모관리 화면 (ui/cs_memo.py)
# ----------------------------------------------------------
# 각 주문 상세내역의 'CS메모(고객 특이사항)' 칸에 적어둔 메모들을 한곳에 모아
# 목록으로 보여줍니다. (샵마인 CS메모관리 화면 구성 참고)
# 행을 클릭하면 오른쪽 상세에서 메모를 보고 바로 수정할 수 있습니다.
# ==========================================================

import pandas as pd
import streamlit as st

from repositories import order_repository
from ui import common

# 목록에 보여줄 컬럼(메모 중심). build_full_row가 만들어주는 키를 골라 씁니다.
_DISPLAY_COLUMNS = [
    "No", "쇼핑몰", "쇼핑몰ID", "주문일(약식)", "주문번호", "CS메모", "구매자", "수령자", "상품명",
]
_RENAME = {"주문일(약식)": "주문일", "CS메모": "메모"}


def render() -> None:
    st.header("CS메모관리")
    st.caption(
        "상세내역에서 적어둔 CS메모(고객 특이사항)가 있는 주문 목록입니다. "
        "행을 클릭하면 오른쪽에서 메모를 보고 수정할 수 있습니다."
    )

    orders = order_repository.list_orders_with_cs_memo()
    if not orders:
        st.info(
            "아직 저장된 CS메모가 없습니다. 각 주문의 상세내역에서 'CS메모' 칸에 적고 저장하면 "
            "여기 한곳에 모여서 보입니다."
        )
        return

    rows = [common.build_full_row(order, idx + 1, reveal=True) for idx, order in enumerate(orders)]

    keyword = st.text_input("CS메모 검색", placeholder="메모 내용, 주문번호, 수령자, 상품명 등으로 검색")
    pairs = [(o, r) for o, r in zip(orders, rows) if common.matches_search(r, keyword)]
    if not pairs:
        st.info("검색 결과가 없습니다.")
        return
    filtered_orders = [pair[0] for pair in pairs]
    filtered_rows = [pair[1] for pair in pairs]

    st.caption(f"총 {len(filtered_rows)}건")

    display_df = pd.DataFrame(filtered_rows)[_DISPLAY_COLUMNS].rename(columns=_RENAME)
    table_height = min(max(len(filtered_rows) + 1, 3) * common.TABLE_ROW_HEIGHT + 3, 600)

    col_table, col_detail = st.columns([2, 1])
    with col_table:
        event = st.dataframe(
            display_df,
            hide_index=True,
            width="stretch",
            height=table_height,
            on_select="rerun",
            selection_mode="single-row",
            key="cs_memo_table",
        )
    with col_detail:
        try:
            selected = event["selection"]["rows"]
        except Exception:
            selected = []
        if len(selected) == 1 and 0 <= selected[0] < len(filtered_orders):
            common.render_full_detail(filtered_orders[selected[0]], reveal=True)
        else:
            st.info("왼쪽 표에서 주문을 클릭하면 여기서 메모를 보고 수정할 수 있습니다.")
