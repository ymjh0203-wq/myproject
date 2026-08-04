# ==========================================================
# 반품주문 화면 (ui/return_orders.py)
# ----------------------------------------------------------
# 쿠팡 공식 API(returnRequests, cancelType=RETURN)로 실제 반품요청 목록을
# 가져옵니다. ("Return/Cancellation Request List Query" 문서, 2026-07-20 확인)
# ==========================================================

import streamlit as st

from ui import common


def render() -> None:
    st.header("반품주문")
    common.render_claim_list(
        claim_type="RETURN",
        title="반품요청",
        empty_message="반품요청 내역이 없습니다. 위 '수집하기'를 눌러보세요.",
    )
