# ==========================================================
# 반품완료(환불완료) 화면 (ui/return_completed.py)
# ----------------------------------------------------------
# 쿠팡 반품요청(returnRequests, cancelType=RETURN) 중 '완료(RETURNS_COMPLETED)'된
# 건, 즉 환불까지 끝난 반품만 보여줍니다. 데이터는 반품주문 화면과 같은 API로
# 수집됩니다(수집하기를 누르면 접수~완료 전 상태를 한 번에 최신화).
# ==========================================================

import streamlit as st

from ui import common


def render() -> None:
    st.header("반품완료(환불완료)")
    common.render_claim_list(
        claim_type="RETURN",
        title="반품",
        empty_message="반품완료 내역이 없습니다. 위 '수집하기'를 눌러보세요.",
        only_completed=True,  # 완료(RETURNS_COMPLETED)된 건만 표시
    )
