# ============================================================
# app_pages/products.py  —  등록 상품 관리 (퍼센티 '등록 상품 관리' 모티브)
# ------------------------------------------------------------
# 오늘 등록 상품수(마켓별) · 검색 필터 · 등록 상품 목록.
# 상품명 검색은 실제로 목록을 걸러줍니다(그 외 필터는 구성만).
# ============================================================

import pandas as pd
import streamlit as st

import config_store as cs

st.title("등록 상품 관리")
st.caption("수집 → 가공 → 등록 상태를 검색하고 관리합니다.")


@st.cache_data(ttl=30)
def load_products(limit: int = 300) -> pd.DataFrame:
    from db import get_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title_original, price_original, currency,
                       sales_count, shop_name, seed_id, match_rank, collected_at
                FROM products_raw ORDER BY id DESC LIMIT %s
                """,
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return pd.DataFrame(rows, columns=cols)
    finally:
        conn.close()


@st.cache_data(ttl=30)
def load_today_uploads() -> dict:
    from db import get_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT marketplace, count(*) FROM listings "
                "WHERE created_at::date = current_date GROUP BY marketplace"
            )
            return dict(cur.fetchall())
    finally:
        conn.close()


# ---- 오늘 등록 상품수 (마켓 배지) ----
with st.container(border=True):
    st.markdown("**오늘 등록 상품수**")
    try:
        today = load_today_uploads()
    except Exception:
        today = {}
    cols = st.columns(len(cs.MARKET_KEYS))
    for col, (key, label) in zip(cols, cs.MARKET_KEYS.items()):
        col.metric(label, f"{today.get(key, 0)} 건")

# ---- 검색 필터 (구성) ----
with st.container(border=True):
    st.markdown("**검색 필터**")
    f1, f2, f3 = st.columns([1, 2, 1])
    with f1:
        st.selectbox("상태", ["전체", "수집완료", "가공중", "검수대기", "검수완료",
                            "등록완료", "판매중", "품절", "실패"])
    with f2:
        st.multiselect("마켓", list(cs.MARKET_KEYS.values()))
    with f3:
        st.selectbox("수집처", ["모든 수집처", "타오바오", "티몰"])
    st.segmented_control("기간", ["전체기간", "오늘", "1주일", "1개월", "3개월"], default="전체기간")

# ---- 단독 검색 (상품명은 실제 필터) ----
with st.container(border=True):
    st.markdown("**단독 검색**")
    s1, s2 = st.columns(2)
    name_q = s1.text_input("상품명", placeholder="상품명 입력")
    s2.text_input("원본 URL", placeholder="원본 URL 입력")

# ---- 등록 상품 목록 ----
st.markdown("**등록 상품 목록**")
try:
    df = load_products()
    if name_q:
        df = df[df["title_original"].fillna("").str.contains(name_q, case=False)]
    left, right = st.columns([3, 1])
    left.caption(f"총 {len(df)}개 상품")
    if right.button("새로고침", icon=":material/refresh:"):
        load_products.clear()
        load_today_uploads.clear()
        st.rerun()
    if df.empty:
        st.caption("조건에 맞는 상품이 없습니다.")
    else:
        st.dataframe(df, hide_index=True, width="stretch")
except Exception as e:
    st.warning("DB 미연결 — 설정에서 DATABASE_URL 을 확인하세요.", icon=":material/warning:")
    with st.expander("자세한 오류"):
        st.code(str(e))
