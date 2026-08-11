# ============================================================
# app.py  —  상품등록 웹 앱 (퍼센티 모티브, 멀티페이지 셸)
# ------------------------------------------------------------
# 실행:
#   벤치마킹소싱_실행.bat  (더블클릭)
#   또는  .venv\Scripts\streamlit.exe run app.py
#
# 사이드바:
#   홈 · 상품 관리(벤치마킹 소싱 / 상품 가공 / 등록 상품 관리) · 설정
#   (주문관리는 별도 앱이라 여기서 제외)
# ============================================================

import streamlit as st

st.set_page_config(
    page_title="상품등록 · 소싱",
    page_icon=":material/storefront:",
    layout="wide",
)

home = st.Page("app_pages/home.py", title="홈", icon=":material/home:", default=True)
sourcing = st.Page("app_pages/sourcing.py", title="벤치마킹 소싱", icon=":material/image_search:")
processing = st.Page("app_pages/processing.py", title="상품 가공", icon=":material/edit_note:")
products = st.Page("app_pages/products.py", title="등록 상품 관리", icon=":material/inventory_2:")
settings = st.Page("app_pages/settings.py", title="설정", icon=":material/settings:")

nav = st.navigation(
    {
        "": [home],
        "상품 관리": [sourcing, processing, products],
        "설정": [settings],
    }
)
nav.run()
