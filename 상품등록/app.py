# ============================================================
# app.py  —  상품등록 웹 앱 (퍼센티 모티브, 멀티페이지 셸)
# ------------------------------------------------------------
# 실행:
#   상품등록_실행.bat  (더블클릭 → 크롬으로 열림)
#   또는  .venv\Scripts\streamlit.exe run app.py
#
# 사이드바(퍼센티 구조를 우리 범위로 맞춤):
#   홈
#   상품 관리 · 벤치마킹 소싱 / 신규 상품 등록 / 등록 상품 관리 / 그룹 상품 관리
#   설정 · 필수 설정 / 키워드·단어 설정
#   (주문관리·배송대행지·장부 등은 범위 밖이라 제외)
# ============================================================

import streamlit as st

st.set_page_config(
    page_title="상품등록 · 소싱",
    page_icon=":material/storefront:",
    layout="wide",
    initial_sidebar_state="expanded",
)

home = st.Page("app_pages/home.py", title="홈", icon=":material/home:", default=True)

sourcing = st.Page("app_pages/sourcing.py", title="벤치마킹 소싱", icon=":material/image_search:")
new_product = st.Page("app_pages/new_product.py", title="신규 상품 등록", icon=":material/add_box:")
products = st.Page("app_pages/products.py", title="등록 상품 관리", icon=":material/inventory_2:")
groups = st.Page("app_pages/groups.py", title="그룹 상품 관리", icon=":material/folder:")

config_page = st.Page("app_pages/config_page.py", title="필수 설정", icon=":material/tune:")
words = st.Page("app_pages/words.py", title="키워드/단어 설정", icon=":material/spellcheck:")

nav = st.navigation(
    {
        "": [home],
        "상품 관리": [sourcing, new_product, products, groups],
        "설정": [config_page, words],
    }
)
nav.run()
