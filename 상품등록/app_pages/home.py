# ============================================================
# app_pages/home.py  —  홈 대시보드 (퍼센티 홈 모티브)
# ------------------------------------------------------------
# 오늘/전체 수집 상품 수, 마켓별 등록(업로드) 현황을 보여줍니다.
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
            cur.execute("SELECT count(*) FROM listings")
            list_total = cur.fetchone()[0]
            cur.execute("SELECT marketplace, count(*) FROM listings GROUP BY marketplace")
            by_market = dict(cur.fetchall())
        return {
            "raw_total": raw_total,
            "raw_today": raw_today,
            "list_total": list_total,
            "by_market": by_market,
        }
    finally:
        conn.close()


st.title("홈")
st.caption("타오바오 소싱 → 가공 → 스마트스토어·옥션/지마켓(ESM) 등록 현황")

# 마켓 연동 안내 배너 (퍼센티의 "마켓 연동하기" 자리)
st.info(
    "스마트스토어·옥션/지마켓(ESM) 등록은 커머스API 키 연동 후 가능합니다. "
    "타오바오 수집은 최초 1회 로그인(타오바오_로그인_설정.bat)이 필요합니다.",
    icon=":material/link:",
)

try:
    c = load_counts()
    db_ok = True
except Exception as e:
    db_ok = False
    c = {"raw_total": 0, "raw_today": 0, "list_total": 0, "by_market": {}}
    st.warning(
        "DB에 연결되지 않았습니다. 설정 페이지에서 DATABASE_URL과 마이그레이션을 확인하세요.",
        icon=":material/warning:",
    )
    with st.expander("자세한 오류"):
        st.code(str(e))

st.subheader("수집 현황")
m1, m2, m3 = st.columns(3)
m1.metric("오늘 수집 상품", f"{c['raw_today']} 건")
m2.metric("전체 수집 상품", f"{c['raw_total']} 건")
m3.metric("전체 등록(업로드)", f"{c['list_total']} 건")

st.subheader("마켓별 등록 현황")
rows = [{"마켓": label, "등록 수": c["by_market"].get(key, 0)} for key, label in MARKETS.items()]
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

if db_ok and c["raw_total"] == 0:
    st.caption("아직 수집된 상품이 없습니다. '벤치마킹 소싱'에서 시작해보세요.")
