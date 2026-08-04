# ==========================================================
# 취소주문 화면 (ui/canceled_orders.py)
# ----------------------------------------------------------
# 쿠팡 공식 API(returnRequests, cancelType=CANCEL)로 실제 취소요청 목록을
# 가져옵니다. ("Return/Cancellation Request List Query" 문서, 2026-07-20 확인)
# ==========================================================

import streamlit as st

from ui import common


def render() -> None:
    st.header("취소주문")
    common.render_claim_list(
        claim_type="CANCEL",
        title="취소요청",
        empty_message="취소요청 내역이 없습니다. 위 '수집하기'를 눌러보세요.",
    )
