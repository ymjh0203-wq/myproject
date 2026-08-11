# ============================================================
# app_pages/settings.py  —  설정 / 연결 상태
# ------------------------------------------------------------
# DB 연결, 타오바오 로그인, 마켓 연동 상태를 확인/안내합니다.
# (비밀번호·키 입력은 여기서 하지 않고, 안내만 합니다.)
# ============================================================

import os

import streamlit as st
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DIR = os.path.join(BASE_DIR, ".browser_profile")

st.title("설정")
st.caption("연결 상태를 확인합니다.")

# ---- DB 연결 상태 ----
st.subheader("Supabase (DB) 연결")
load_dotenv(os.path.join(BASE_DIR, ".env"))
db_url = os.getenv("DATABASE_URL")
if not db_url:
    st.error("DATABASE_URL 이 설정되지 않았습니다.", icon=":material/error:")
    st.markdown(
        "`.env.example` 를 복사해 `.env` 로 만들고, Supabase → Settings → Database → "
        "Connection string(URI) 값을 `DATABASE_URL=` 뒤에 붙여넣으세요. "
        "그다음 `python apply_migration.py` 로 표(001·002)를 만드세요."
    )
else:
    try:
        from db import get_conn

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
                cur.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND table_name IN ('products_raw','products_processed',"
                    "'keywords','category_mapping','listings','benchmark_seeds')"
                )
                table_count = cur.fetchone()[0]
        finally:
            conn.close()
        st.success(f"연결 성공 · 필요한 표 {table_count}/6개 확인", icon=":material/check_circle:")
        if table_count < 6:
            st.warning("표가 부족합니다. `python apply_migration.py` 를 실행하세요.", icon=":material/warning:")
    except Exception as e:
        st.error("연결 실패", icon=":material/error:")
        with st.expander("자세한 오류"):
            st.code(str(e))

# ---- 타오바오 로그인 상태 ----
st.subheader("타오바오 로그인")
if os.path.isdir(PROFILE_DIR) and os.listdir(PROFILE_DIR):
    st.success("로그인 프로필이 저장돼 있습니다(.browser_profile).", icon=":material/check_circle:")
else:
    st.warning(
        "아직 로그인 프로필이 없습니다. `타오바오_로그인_설정.bat` 을 더블클릭해 "
        "브라우저에서 1회 로그인해주세요.",
        icon=":material/warning:",
    )

# ---- 마켓 연동 상태 (아직 미구현) ----
st.subheader("마켓 연동")
st.dataframe(
    {"마켓": ["스마트스토어", "옥션/G마켓(ESM)"], "상태": ["미연동", "미연동"]},
    hide_index=True,
    width="stretch",
)
st.caption("커머스API·ESM PLUS 키 연동은 이후 단계에서 추가합니다.")
