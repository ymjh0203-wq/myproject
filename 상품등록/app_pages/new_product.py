# ============================================================
# app_pages/new_product.py  —  신규 상품 등록 (퍼센티 '신규 상품 등록' 모티브)
# ------------------------------------------------------------
# 타오바오 상품 URL 을 넣어 수집 → products_raw 저장. 아래에 수집 상품 목록.
# (퍼센티의 원클릭 수집/엑셀 일괄 수집은 우리 범위에선 타오바오 URL 수집으로 대체)
# ============================================================

from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st

from collector.taobao import CaptchaDetected, collect_taobao

st.title("신규 상품 등록")
st.caption("타오바오 상품을 수집해 등록 후보로 만듭니다.")

st.info(
    "타오바오 상품 상세 URL 을 넣어 수집합니다. 수집 시 타오바오 창이 뜨고, "
    "로그인/캡차가 보이면 그 창에서 직접 처리하세요. "
    "이미지로 유사 상품을 찾으려면 왼쪽 '벤치마킹 소싱'을 쓰세요.",
    icon=":material/info:",
)

# ---- 지원 수집처 (현재 타오바오 중심) ----
with st.container(border=True):
    st.markdown("**지원 수집처**")
    st.markdown(
        ":green-badge[Taobao] "
        ":gray-badge[TMALL(예정)] :gray-badge[1688(예정)] :gray-badge[VVIC(예정)]"
    )

# ---- 단일 URL 수집 ----
with st.form("collect_form"):
    url = st.text_input("타오바오 상품 URL", placeholder="https://item.taobao.com/item.htm?id=...")
    submitted = st.form_submit_button("수집하기", type="primary", icon=":material/download:")

if submitted:
    if "taobao.com" not in url and "tmall.com" not in url:
        st.warning("타오바오/티몰 상품 상세 URL 을 입력해주세요.")
    else:
        with st.status("수집 중...", expanded=True) as status:
            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    data = ex.submit(collect_taobao, url).result()
                st.write(f"상품명: {data.get('title_original')}")
                st.write(f"가격(CNY): {data.get('price_original')}")
                try:
                    from db import get_conn, upsert_product_raw
                    conn = get_conn()
                    try:
                        pid = upsert_product_raw(conn, data)
                        st.write(f"products_raw #{pid} 저장")
                    finally:
                        conn.close()
                    status.update(label="수집·저장 완료", state="complete")
                except Exception as e:
                    status.update(label="수집됨(저장은 실패)", state="error")
                    st.warning(f"DB 저장 실패(추출은 성공): {e}")
            except CaptchaDetected as e:
                status.update(label="캡차/차단으로 중단", state="error")
                st.error(f"{e}", icon=":material/report:")
            except Exception as e:
                status.update(label="오류로 중단", state="error")
                st.error(f"수집 실패: {e}", icon=":material/report:")


# ---- 수집 상품 목록 ----
st.divider()
st.markdown("**수집 상품 목록**")


@st.cache_data(ttl=30)
def load_raw(limit: int = 50) -> pd.DataFrame:
    from db import get_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title_original, price_original, sales_count, shop_name, collected_at "
                "FROM products_raw ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return pd.DataFrame(rows, columns=cols)
    finally:
        conn.close()


try:
    df = load_raw()
    if st.button("새로고침", icon=":material/refresh:"):
        load_raw.clear()
        st.rerun()
    if df.empty:
        st.caption("수집된 상품이 없습니다. 위에서 URL 을 넣어 수집해보세요.")
    else:
        st.dataframe(df, hide_index=True, width="stretch")
except Exception as e:
    st.caption("DB 미연결 — 설정에서 DATABASE_URL 을 확인하세요.")
    with st.expander("자세한 오류"):
        st.code(str(e))
