# ============================================================
# app_pages/products.py  —  등록 상품 관리
# ------------------------------------------------------------
# 수집(products_raw) / 가공(products_processed) / 등록(listings) 현황을
# 표로 보여줍니다. DB(DATABASE_URL)가 필요합니다.
# ============================================================

import pandas as pd
import streamlit as st

st.title("등록 상품 관리")
st.caption("수집 → 가공 → 등록 상태를 한눈에 봅니다.")


@st.cache_data(ttl=30)
def load_raw(limit: int = 200) -> pd.DataFrame:
    from db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title_original, price_original, currency,
                       sales_count, shop_name, seed_id, match_rank, collected_at
                FROM products_raw
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return pd.DataFrame(rows, columns=cols)
    finally:
        conn.close()


try:
    df = load_raw()
    st.markdown(f"**수집된 상품 (products_raw) — {len(df)}건**")
    if df.empty:
        st.caption("아직 수집된 상품이 없습니다. '벤치마킹 소싱'에서 시작해보세요.")
    else:
        st.dataframe(df, hide_index=True, width="stretch")
    if st.button("새로고침", icon=":material/refresh:"):
        load_raw.clear()
        st.rerun()
except Exception as e:
    st.warning(
        "DB에 연결되지 않았습니다. 설정 페이지에서 DATABASE_URL과 마이그레이션을 확인하세요.",
        icon=":material/warning:",
    )
    with st.expander("자세한 오류"):
        st.code(str(e))
