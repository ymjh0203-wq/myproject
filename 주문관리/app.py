# ==========================================================
# 프로그램 시작점 (app.py)
# ----------------------------------------------------------
# *** 이 프로그램은 이 파일로 실행합니다. ***
#
#   실행 방법 (명령 프롬프트 / PowerShell에서, 이 폴더 안으로 이동한 뒤):
#       streamlit run app.py
#
# 하는 일:
#   1. 데이터베이스 테이블이 없으면 만든다
#   2. 상단에 전체 메뉴 탭을 보여준다
#   3. 각 탭 안에서 해당 화면(ui/*.py)을 그린다
#
# 메뉴 구성 안내:
#   탭 이름 옆에는 숫자를 표시하지 않습니다. (예전에는 "신규주문 ~ 구매확정"
#   옆에 우리 DB에 누적된 건수를 표시했었는데, 각 화면 안의 "쿠팡 기준 현재
#   N건" 실시간 배너와 헷갈린다는 피드백이 있어서 뺐습니다. 정확한 현재
#   건수는 각 화면에 들어가서 확인하시면 됩니다.)
# ==========================================================

import streamlit as st

import database
from repositories import market_repository
from ui import (
    canceled_orders,
    cs_memo,
    delivered,
    exchange_orders,
    home,
    integrated_orders,
    new_orders,
    purchase_confirmed,
    ready_to_ship,
    return_completed,
    return_orders,
    settings,
    shipping,
    urgent_inquiries,
)

st.set_page_config(page_title="주문관리", layout="wide")

# 앱이 실행될 때마다 테이블이 있는지 확인합니다. 이미 있으면 아무 일도 안 일어납니다.
database.init_db()
# .env에 있던 쿠팡 계정을 마켓 계정 목록에도 자동으로 등록해둡니다 (이미 있으면 건너뜀).
market_repository.ensure_default_coupang_account()

st.title("주문관리")

# (표시할 이름, 실제로 그릴 화면 함수) 순서쌍의 목록입니다.
# 이 목록의 순서가 그대로 상단 탭 메뉴 순서가 됩니다.
MENU_ITEMS = [
    ("홈", home.render),
    ("신규주문", new_orders.render),
    ("발송대기", ready_to_ship.render),
    ("배송중", shipping.render),
    ("배송완료", delivered.render),
    ("구매확정", purchase_confirmed.render),
    ("취소주문", canceled_orders.render),
    ("반품주문", return_orders.render),
    ("교환주문", exchange_orders.render),
    ("통합주문관리", integrated_orders.render),
    ("CS메모관리", cs_memo.render),
    ("긴급/문의관리", urgent_inquiries.render),
    ("반품완료(환불완료)", return_completed.render),
    ("쇼핑몰 계정", settings.render_shop_accounts),
    ("일반 설정", settings.render_general),
]

# 화면 이름 → 그리는 함수 (위 MENU_ITEMS를 사전으로)
VIEWS = dict(MENU_ITEMS)

# 단계들은 상단에 가로로 쭉 '나열'하고, '설정'만 드롭다운(펼치기)으로 둡니다.
FLAT_STAGES = [
    "홈", "신규주문", "발송대기", "배송중", "배송완료", "구매확정",
    "취소주문", "반품주문", "교환주문", "통합주문관리", "CS메모관리",
    "긴급/문의관리", "반품완료(환불완료)",
]
SETTINGS_ITEMS = ["쇼핑몰 계정", "일반 설정"]

if "current_view" not in st.session_state:
    st.session_state["current_view"] = "홈"
# 존재하지 않는 화면 이름이 세션에 남아 있으면(예: 코드 변경) 홈으로 되돌립니다.
if st.session_state["current_view"] not in VIEWS:
    st.session_state["current_view"] = "홈"

current = st.session_state["current_view"]

# ---- 상단 메뉴: 단계 가로 나열 + 설정 드롭다운 ----
with st.container(horizontal=True):
    for name in FLAT_STAGES:
        if st.button(
            name,
            key=f"nav_{name}",
            type="primary" if current == name else "secondary",
        ):
            st.session_state["current_view"] = name
            st.rerun()
    # '설정'만 펼치기(드롭다운). 안에 쇼핑몰 계정 / 일반 설정.
    with st.popover(
        "설정",
        type="primary" if current in SETTINGS_ITEMS else "secondary",
    ):
        for name in SETTINGS_ITEMS:
            if st.button(name, key=f"navset_{name}", width="stretch"):
                st.session_state["current_view"] = name
                st.rerun()

st.divider()

# ---- 현재 선택된 화면 그리기 ----
VIEWS[current]()
