# ============================================================
# app_pages/home.py  —  홈 대시보드 (퍼센티 홈의 기능 부분만)
# ------------------------------------------------------------
# 오늘/전체 수집 상품 수 + 마켓별 등록(업로드) 현황.
# (개인용이라 체험플랜/공지/FAQ 같은 SaaS 카드는 넣지 않음)
# DB(DATABASE_URL)가 없으면 안내만 표시합니다.
# ============================================================

import pandas as pd
import streamlit as st

MARKETS = {"smartstore": "스마트스토어", "esm": "옥션/G마켓(ESM)"}


@st.cache_data(ttl=60)
def load_counts() -> dict:
    """수집/등록 현황을 DB에서 집계합니다(60초 캐시)."""
    from db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM products_raw")
            raw_total = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM products_raw WHERE collected_at::date = current_date"
            )
            raw_today = cur.fetchone()[0]
            cur.execute("SELECT marketplace, count(*) FROM listings GROUP BY marketplace")
            upload_total = dict(cur.fetchall())
            cur.execute(
                "SELECT marketplace, count(*) FROM listings "
                "WHERE created_at::date = current_date GROUP BY marketplace"
            )
            upload_today = dict(cur.fetchall())
        return {
            "raw_total": raw_total,
            "raw_today": raw_today,
            "upload_total": upload_total,
            "upload_today": upload_today,
        }
    finally:
        conn.close()


def market_table(counts_by_market: dict) -> pd.DataFrame:
    """마켓별 등록 수 표를 만듭니다(미연동 마켓은 0)."""
    rows = [
        {"마켓": label, "등록 수": counts_by_market.get(key, 0)}
        for key, label in MARKETS.items()
    ]
    return pd.DataFrame(rows)


st.title("홈")
st.caption("타오바오 소싱 → 가공 → 스마트스토어·옥션/지마켓(ESM) 등록 현황")


@st.cache_data(ttl=1800)
def load_fx() -> dict:
    import fx
    return fx.get_rates()


# ---- 실시간 환율 (무료 ECB 기준, 일 단위 갱신) ----
with st.container(border=True):
    try:
        rate = load_fx()
        head = st.columns([3, 1])
        head[0].markdown("**실시간 환율**")
        head[1].caption(f"{rate['date']} 기준 · ECB")
        cols = st.columns(len(rate["rows"]))
        for col, row in zip(cols, rate["rows"]):
            up = row["change"] >= 0
            arrow = "▲" if up else "▼"
            color = "red" if up else "blue"
            col.caption(f"{row['name']} ({row['code']})")
            col.markdown(f"**{row['krw']:,.2f}원**")
            col.markdown(f":{color}[{arrow} {abs(row['change']):.2f}%]")
    except Exception:
        st.caption("환율 정보를 불러오지 못했습니다(네트워크 확인).")

try:
    c = load_counts()
except Exception as e:
    c = None
    st.warning(
        "DB에 연결되지 않았습니다. 설정 페이지에서 DATABASE_URL과 마이그레이션을 확인하세요.",
        icon=":material/warning:",
    )
    with st.expander("자세한 오류"):
        st.code(str(e))

if c is None:
    c = {"raw_total": 0, "raw_today": 0, "upload_total": {}, "upload_today": {}}

today_col, total_col = st.columns(2)

with today_col:
    with st.container(border=True):
        st.subheader("오늘")
        st.metric("오늘 수집 상품", f"{c['raw_today']} 건")
        st.markdown("**오늘 마켓별 등록**")
        st.dataframe(market_table(c["upload_today"]), hide_index=True, width="stretch")

with total_col:
    with st.container(border=True):
        st.subheader("전체")
        st.metric("전체 수집 상품", f"{c['raw_total']} 건")
        st.markdown("**전체 마켓별 등록**")
        st.dataframe(market_table(c["upload_total"]), hide_index=True, width="stretch")

if c["raw_total"] == 0:
    st.caption("아직 수집된 상품이 없습니다. 왼쪽 '벤치마킹 소싱'에서 시작해보세요.")
