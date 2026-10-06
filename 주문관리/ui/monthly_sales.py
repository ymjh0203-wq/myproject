# ==========================================================
# 월별 매출 집계 화면 (ui/monthly_sales.py)
# ----------------------------------------------------------
# 결제일(paid_at) 기준으로 월별 매출을 집계해 보여줍니다.
#   - 마켓(상점) 선택 + 월 선택 필터
#   - 최근 6개월 현황 요약
#   - 그래프 너비·높이 조절
# ==========================================================

import pandas as pd
import streamlit as st

from repositories import order_repository

_METRIC_COLS = ["건수", "판매금액", "수수료", "정산금액", "배송비"]
_BLUE = ["#1D4ED8", "#60A5FA"]  # 판매금액(진), 정산금액(연)


def _won(n) -> str:
    try:
        return f"{int(n):,}원"
    except (TypeError, ValueError):
        return "-"


def _fmt_table(df: pd.DataFrame) -> pd.DataFrame:
    show = df.copy()
    for col in ("판매금액", "수수료", "정산금액", "배송비"):
        if col in show.columns:
            show[col] = show[col].map(lambda v: f"{int(v):,}")
    if "건수" in show.columns:
        show["건수"] = show["건수"].map(lambda v: f"{int(v):,}")
    return show


def _sized_chart(chart_df, width_pct: int, height_px: int, stack, color=None) -> None:
    """너비(%)는 컬럼으로, 높이(px)는 차트 height로 조절해 막대그래프를 그립니다."""
    def _draw():
        st.bar_chart(chart_df, height=height_px, stack=stack, color=color)

    if width_pct >= 100:
        _draw()
    else:
        c, _ = st.columns([width_pct, max(1, 100 - width_pct)])
        with c:
            _draw()


def render() -> None:
    st.header("월별 매출")
    st.caption(
        "**결제일 기준**으로 월별 판매금액·수수료·정산금액을 집계합니다. "
        "정산은 실제 정산이 반영된 건은 실값, 아직 안 된 건은 수수료 12% 가정 추정치입니다."
    )

    top = st.columns(3)
    with top[0]:
        include_closed = st.checkbox(
            "취소·반품(주문종료)도 포함", value=False,
            help="기본은 매출에서 취소·반품 건을 제외합니다.",
        )

    data = order_repository.monthly_sales_detailed(exclude_closed=not include_closed)
    if not data:
        st.info("집계할 주문이 없습니다.")
        return

    df = pd.DataFrame(data)
    all_months = sorted(df["월"].unique())
    recent_months = all_months[-6:]
    df6 = df[df["월"].isin(recent_months)]
    markets = sorted(df["상점"].unique())

    with top[1]:
        sel_market = st.selectbox("마켓(상점) 선택", ["전체"] + markets, key="ms_market")
    with top[2]:
        sel_month = st.selectbox("월 선택", ["최근 6개월 전체"] + list(reversed(all_months)), key="ms_month")

    # ---- 그래프 크기 조절 ----
    sc = st.columns([1, 1, 3])
    with sc[0]:
        chart_w = st.slider("그래프 너비(%)", 40, 100, 100, 5, key="ms_cw")
    with sc[1]:
        chart_h = st.slider("그래프 높이(px)", 250, 900, 400, 50, key="ms_ch")

    def _by_market(d):
        return d if sel_market == "전체" else d[d["상점"] == sel_market]

    # ---- 최근 6개월 현황 요약(선택 마켓 기준) ----
    st.subheader(f"최근 6개월 현황 — {sel_market}")
    f6 = _by_market(df6)
    tot_cnt = int(f6["건수"].sum())
    tot_sales = int(f6["판매금액"].sum())
    tot_settle = int(f6["정산금액"].sum())
    n_months = max(f6["월"].nunique(), 1)
    m = st.columns(4)
    m[0].metric("6개월 건수", f"{tot_cnt:,}")
    m[1].metric("6개월 판매금액", _won(tot_sales))
    m[2].metric("6개월 정산금액", _won(tot_settle))
    m[3].metric("월평균 판매", _won(tot_sales / n_months))

    st.divider()

    if sel_month == "최근 6개월 전체":
        # ===== 최근 6개월 전체 보기 =====
        by_month6 = _by_market(df6).groupby("월", as_index=False)[_METRIC_COLS].sum().sort_values("월")

        st.subheader(f"최근 6개월 판매·정산 — {sel_market}")
        if not by_month6.empty:
            _sized_chart(by_month6.set_index("월")[["판매금액", "정산금액"]], chart_w, chart_h, stack=False, color=_BLUE)

        st.subheader("마켓별 월별 판매금액 (최근 6개월)")
        pivot = df6.pivot_table(index="월", columns="상점", values="판매금액", aggfunc="sum", fill_value=0).sort_index()
        if not pivot.empty:
            _sized_chart(pivot, chart_w, chart_h, stack=True)

        st.subheader(f"월별 표 — {sel_market}")
        st.dataframe(_fmt_table(by_month6.sort_values("월", ascending=False)), width="stretch", hide_index=True)
        st.caption("💡 위 '월 선택'에서 특정 월을 고르면 그 달의 **일별 매출**(하루하루)을 볼 수 있습니다.")
    else:
        # ===== 특정 월 보기(그 달 마켓별 상세) =====
        month_df = df[df["월"] == sel_month]
        fm = _by_market(month_df)

        st.subheader(f"{sel_month} 요약 — {sel_market}")
        mm = st.columns(3)
        mm[0].metric("건수", f"{int(fm['건수'].sum()):,}")
        mm[1].metric("판매금액", _won(fm["판매금액"].sum()))
        mm[2].metric("정산금액", _won(fm["정산금액"].sum()))

        st.subheader(f"{sel_month} 마켓별 판매·정산")
        by_market = month_df.groupby("상점", as_index=False)[_METRIC_COLS].sum().sort_values("판매금액", ascending=False)
        if not by_market.empty:
            _sized_chart(by_market.set_index("상점")[["판매금액", "정산금액"]], chart_w, chart_h, stack=False, color=_BLUE)
        st.dataframe(_fmt_table(by_market), width="stretch", hide_index=True)

        # ===== 그 달의 '일별 매출' =====
        st.divider()
        st.subheader(f"{sel_month} 일별 매출 — {sel_market}")
        daily = order_repository.daily_sales_detailed(sel_month, exclude_closed=not include_closed)
        if not daily:
            st.info("이 달에 집계할 일별 매출이 없습니다.")
        else:
            ddf = pd.DataFrame(daily)
            dfm = _by_market(ddf)
            by_day = dfm.groupby("일자", as_index=False)[_METRIC_COLS].sum().sort_values("일자")
            if not by_day.empty:
                _sized_chart(by_day.set_index("일자")[["판매금액", "정산금액"]], chart_w, chart_h, stack=False, color=_BLUE)
            st.dataframe(
                _fmt_table(by_day.sort_values("일자", ascending=False)), width="stretch", hide_index=True
            )
