# ==========================================================
# 점검판 화면 (ui/inspection.py)
# ----------------------------------------------------------
# '막혀 있는 주문'을 한곳에 모아 보여줍니다. 탭을 일일이 돌지 않아도
# 손봐야 할 건(배송 지연·반품 미처리·발송 지연)을 바로 알 수 있습니다.
#   - 배송중인데 오래(기본 30일) 안 움직이는 주문
#   - 반품접수 후 오래(기본 7일) 미처리인 건
#   - 발송대기에서 오래(기본 7일) 머문 주문
# ==========================================================

from datetime import date

import pandas as pd
import streamlit as st

import models
from repositories import claims_repository, order_repository

_RESOLVED_RETURN = "RETURNS_COMPLETED"


def _go_button(label: str, view_name: str, key: str) -> None:
    """해당 탭으로 바로 이동하는 버튼."""
    if st.button(label, key=key):
        st.session_state["current_view"] = view_name
        st.query_params["view"] = view_name
        st.rerun()


def _render_order_section(title: str, help_text: str, rows: list, go_label: str, go_view: str, key: str) -> None:
    st.subheader(f"{title} — {len(rows)}건")
    st.caption(help_text)
    if not rows:
        st.success("해당되는 주문이 없습니다. 👍")
        return
    table = [{
        "주문번호": r.get("market_order_id"),
        "구매자": r.get("orderer_name") or "-",
        "주문일": (r.get("ordered_at") or "")[:10],
        "쿠팡상태": r.get("market_status") or "-",
        "GR(배대지)": r.get("quickstar_order_no") or "-",
        "경과일": r.get("days_elapsed") if r.get("days_elapsed") is not None else "-",
    } for r in rows]
    df = pd.DataFrame(table).sort_values("경과일", ascending=False, key=lambda s: pd.to_numeric(s, errors="coerce"))
    st.dataframe(df, width="stretch", hide_index=True)
    _go_button(go_label, go_view, key)


def render() -> None:
    st.header("점검판")
    st.caption(
        "손봐야 할 '막힌 주문'을 한곳에 모았습니다. 각 기준(일수)은 아래에서 조절할 수 있고, "
        "표 아래 버튼으로 해당 화면에 바로 갈 수 있습니다."
    )

    c = st.columns(3)
    with c[0]:
        ship_days = st.slider("배송중 지연 기준(일)", 7, 90, 30, 1, key="insp_ship_days",
                              help="배송중 상태로 이 일수 이상 안 움직인 주문을 표시합니다.")
    with c[1]:
        return_days = st.slider("반품 미처리 기준(일)", 1, 30, 7, 1, key="insp_return_days",
                                help="반품접수 후 이 일수 이상 완료되지 않은 건을 표시합니다.")
    with c[2]:
        rts_days = st.slider("발송대기 지연 기준(일)", 1, 30, 7, 1, key="insp_rts_days",
                             help="발송대기 상태로 이 일수 이상 머문 주문을 표시합니다.")

    st.divider()

    # 1) 배송중 지연 (핵심)
    stuck_ship = order_repository.orders_stuck_in_status(models.WORK_STATUS_SHIPPING, ship_days)
    _render_order_section(
        f"🚚 배송중 {ship_days}일+ 안 움직이는 주문",
        f"배송중이 된 지 {ship_days}일이 지났는데 아직 배송완료로 넘어가지 않은 주문입니다. "
        "배송 지연·분실·누락 가능성이 있으니 배송조회나 배대지 확인이 필요합니다.",
        stuck_ship, "→ 배송중 화면으로", "배송중", "insp_go_ship",
    )

    st.divider()

    # 2) 반품접수 미처리 오래된 건
    today = date.today()
    pending_returns = []
    for cl in claims_repository.list_claims("RETURN"):
        if (cl.get("receipt_status") or "") == _RESOLVED_RETURN:
            continue  # 완료된 반품은 제외
        req = (cl.get("requested_at") or "")[:10]
        try:
            elapsed = (today - date.fromisoformat(req)).days
        except (TypeError, ValueError):
            elapsed = None
        if elapsed is not None and elapsed >= return_days:
            pending_returns.append({**cl, "days_elapsed": elapsed})

    st.subheader(f"↩️ 반품접수 {return_days}일+ 미처리 — {len(pending_returns)}건")
    st.caption(f"반품접수 후 {return_days}일이 지나도록 완료(환불완료)되지 않은 건입니다. "
               "환불·회수 처리가 지연되고 있는지 확인하세요.")
    if not pending_returns:
        st.success("오래 미처리된 반품이 없습니다. 👍")
    else:
        rdf = pd.DataFrame([{
            "주문번호": r.get("market_order_id"),
            "상점": r.get("market_account_name") or "-",
            "접수일": (r.get("requested_at") or "")[:10],
            "반품상태": r.get("receipt_status") or "-",
            "사유": r.get("reason_category1") or r.get("reason_detail") or "-",
            "경과일": r.get("days_elapsed"),
        } for r in pending_returns]).sort_values("경과일", ascending=False)
        st.dataframe(rdf, width="stretch", hide_index=True)
        _go_button("→ 반품주문 화면으로", "반품주문", "insp_go_return")

    st.divider()

    # 3) 발송대기 지연
    stuck_rts = order_repository.orders_stuck_in_status(models.WORK_STATUS_READY_TO_SHIP, rts_days)
    _render_order_section(
        f"📦 발송대기 {rts_days}일+ 머문 주문",
        f"발송대기 상태로 {rts_days}일 이상 머문 주문입니다. 송장 입력·배대지 접수가 "
        "누락되지 않았는지 확인하세요.",
        stuck_rts, "→ 발송대기 화면으로", "발송대기", "insp_go_rts",
    )
