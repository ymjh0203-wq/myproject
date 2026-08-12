# ============================================================
# app_pages/groups.py  —  그룹 상품 관리 (퍼센티 '그룹 상품 관리' 모티브)
# ------------------------------------------------------------
# 상품을 그룹으로 묶어 관리합니다. 그룹 정의는 data/word_rules.json 과 별개로
# 추후 DB(또는 로컬)로 확장합니다. 현재는 구성(레이아웃) 위주입니다.
# ============================================================

import pandas as pd
import streamlit as st

st.title("그룹 상품 관리")
st.caption("상품 그룹을 지정하고 관리합니다.")

with st.container(border=True):
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        st.markdown("**그룹 관리**")
        st.button("그룹 관리하기", icon=":material/folder:")
    with c2:
        st.markdown("**그룹상품 보기**")
        st.toggle("그룹으로 보기", value=True)
    with c3:
        st.markdown("**그룹 검색**")
        st.caption("현재 생성된 그룹이 없습니다.")

st.markdown("**전체 상품 목록**")


@st.cache_data(ttl=30)
def load_products(limit: int = 300) -> pd.DataFrame:
    from db import get_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title_original, price_original, shop_name, collected_at "
                "FROM products_raw ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return pd.DataFrame(rows, columns=cols)
    finally:
        conn.close()


try:
    df = load_products()
    if df.empty:
        st.caption("등록된 상품이 없습니다. '신규 상품 등록'에서 수집해보세요.")
    else:
        st.dataframe(df, hide_index=True, width="stretch")
except Exception as e:
    st.warning("DB 미연결 — 설정에서 DATABASE_URL 을 확인하세요.", icon=":material/warning:")
    with st.expander("자세한 오류"):
        st.code(str(e))
