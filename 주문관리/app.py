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
import models
from repositories import market_repository, settings_repository
from ui import (
    common,
    canceled_orders,
    cs_memo,
    delivered,
    exchange_orders,
    excel_templates,
    home,
    integrated_orders,
    monthly_sales,
    new_orders,
    purchase_confirmed,
    ready_to_ship,
    return_compensation,
    return_completed,
    return_orders,
    sales_ledger,
    settings,
    shipping,
    sms_log,
    taobao_links,
    urgent_inquiries,
)

st.set_page_config(page_title="주문관리", page_icon="🛒", layout="wide")

# 앱이 실행될 때마다 테이블이 있는지 확인합니다. 이미 있으면 아무 일도 안 일어납니다.
database.init_db()
# .env에 있던 쿠팡 계정을 마켓 계정 목록에도 자동으로 등록해둡니다 (이미 있으면 건너뜀).
market_repository.ensure_default_coupang_account()

# 앱을 처음 열면(세션 첫 실행) 신규주문·발송대기를 자동으로 백그라운드 수집합니다.
# 각 탭에 저장된 기간(빠른선택이면 '오늘까지'로 자동 재계산)을 그대로 씁니다.
# 백그라운드라 화면은 바로 뜨고, 해당 탭에 들어가면 결과가 반영됩니다.
if not st.session_state.get("_auto_collected_once"):
    st.session_state["_auto_collected_once"] = True
    try:
        from services import collect_worker

        _pf_n, _pt_n = common.effective_period_for("new_orders")
        _pf_r, _pt_r = common.effective_period_for("rts")
        collect_worker.start("new_orders", [models.WORK_STATUS_NEW], _pf_n, _pt_n, reconcile=True)
        collect_worker.start("rts", [models.WORK_STATUS_READY_TO_SHIP], _pf_r, _pt_r, reconcile=True)
    except Exception:
        # 자동 수집이 실패해도 앱은 정상적으로 뜨게 합니다(수동 '수집하기'로 재시도 가능).
        pass

# ------------------------------------------------------------------
# 화면(메뉴)을 옮겨다녀도 각 화면의 '입력값'이 초기화되지 않게 유지합니다.
# Streamlit은 '이번 실행에서 그려지지 않은 위젯'(예: 지금 안 보는 다른 단계 화면의
# 기간·검색어 등)의 값을 정리해 버립니다. 그래서 신규주문에서 뭔가 하다가
# 긴급/문의관리로 갔다 오면 신규주문 화면이 초기화됩니다.
# 매 실행마다 해당 입력 위젯 키를 '다시 짚어' 주면, 안 그려진 화면의 값도 유지됩니다.
# (버튼류 키는 session_state로 값 설정이 금지되어 있어 제외합니다. 기간선택/검색처럼
#  실제로 초기화되는 입력 위젯만 접미사로 골라 유지합니다. 화면을 그리기 '전에' 실행.)
_PERSIST_KEY_SUFFIXES = (
    "_period_from", "_period_to", "_quick_period", "_quick_period_applied", "_search",
    "_sort_col", "_sort_dir",
)
for _persist_key in list(st.session_state.keys()):
    if isinstance(_persist_key, str) and _persist_key.endswith(_PERSIST_KEY_SUFFIXES):
        try:
            st.session_state[_persist_key] = st.session_state[_persist_key]
        except Exception:
            pass

st.title("주문관리")

# 새 신규주문/취소/반품/CS문의가 들어오면 여기(모든 화면 상단)에 배너 + 알림음으로 알립니다.
common.render_alarm_bar()

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
    ("반품 보상관리", return_compensation.render),
    ("쇼핑몰 계정", settings.render_shop_accounts),
    ("일반 설정", settings.render_general),
    ("엑셀 양식", excel_templates.render),
    ("문자 발송 이력", sms_log.render),
    ("월별 매출", monthly_sales.render),
    ("매출정리", sales_ledger.render),
    ("타오바오 링크", taobao_links.render),
]

# 화면 이름 → 그리는 함수 (위 MENU_ITEMS를 사전으로)
VIEWS = dict(MENU_ITEMS)

# 단계들은 상단에 가로로 쭉 '나열'하고, '설정'만 드롭다운(펼치기)으로 둡니다.
FLAT_STAGES = [
    "홈", "신규주문", "발송대기", "배송중", "배송완료", "구매확정",
    "취소주문", "반품주문", "교환주문", "통합주문관리", "CS메모관리",
    "긴급/문의관리", "반품완료(환불완료)", "반품 보상관리",
]
SETTINGS_ITEMS = ["월별 매출", "매출정리", "타오바오 링크", "쇼핑몰 계정", "일반 설정", "엑셀 양식", "문자 발송 이력"]

# 홈 화면의 색깔 줄 카드(주문·클레임 현황)에서 ?nav=화면이름 링크로 들어오면 그 화면으로 이동합니다.
_nav_param = st.query_params.get("nav")
if _nav_param and _nav_param in VIEWS:
    st.session_state["current_view"] = _nav_param
    st.query_params["view"] = _nav_param
    del st.query_params["nav"]

# ★현재 화면을 URL(?view=)에 저장 → F5(새로고침)해도 그 단계에 그대로 남습니다.
# (세션은 F5하면 초기화되지만 URL은 유지되므로, URL의 view를 우선으로 복원합니다.)
_view_param = st.query_params.get("view")
if _view_param and _view_param in VIEWS:
    st.session_state["current_view"] = _view_param

if st.session_state.get("current_view") not in VIEWS:
    st.session_state["current_view"] = "홈"

current = st.session_state["current_view"]

# ---- 상단 메뉴: 단계 가로 나열 + 설정 드롭다운 ----
# 샵마인 스타일 '탭 줄' 모양으로 보이도록 CSS를 입힙니다. 네비게이션 버튼(st-key-nav_)에만
# 적용되도록 스코프해서 다른 버튼은 건드리지 않습니다.
st.markdown(
    """
    <style>
    div[data-testid="stHorizontalBlock"]:has([class*="st-key-nav_"]) {
        gap: 2px !important;
        align-items: flex-end !important;
        border-bottom: 1px solid var(--border, rgba(49,51,63,0.15)) !important;
        margin-bottom: 0.25rem !important;
    }
    [class*="st-key-nav_"] button {
        border-radius: 8px 8px 0 0 !important;
        border: 1px solid var(--border, rgba(49,51,63,0.15)) !important;
        border-bottom: none !important;
        background: var(--secondary-background-color, #f0f2f6) !important;
        color: var(--text-color, #31333F) !important;
        opacity: 0.7;
        font-weight: 400 !important;
        padding: 5px 13px !important;
        min-height: 32px !important;
        transition: none !important;
    }
    [class*="st-key-nav_"] button:hover {
        opacity: 1;
        background: var(--background-color, #ffffff) !important;
    }
    [class*="st-key-nav_"] button[data-testid="stBaseButton-primary"] {
        background: var(--background-color, #ffffff) !important;
        color: #185FA5 !important;
        opacity: 1;
        border-bottom: 2px solid #185FA5 !important;
        font-weight: 500 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
# ★탭 순서: 사용자가 드래그로 바꾼 순서를 저장(nav_stage_order)해서 그 순서로 그립니다.
#   저장값에 없는(나중에 추가된) 단계는 뒤에 자동으로 붙입니다. 저장 전엔 기본 순서.
_saved_nav_order = settings_repository.get_setting("nav_stage_order")
if _saved_nav_order:
    _nav_order = [s for s in _saved_nav_order.split("|") if s in FLAT_STAGES]
    _nav_order += [s for s in FLAT_STAGES if s not in _nav_order]
else:
    _nav_order = list(FLAT_STAGES)

with st.container(horizontal=True):
    for name in _nav_order:
        if st.button(
            name,
            key=f"nav_{name}",
            type="primary" if current == name else "secondary",
        ):
            st.session_state["current_view"] = name
            st.query_params["view"] = name
            st.rerun()
    # '설정'만 펼치기(드롭다운). 안에 쇼핑몰 계정 / 일반 설정.
    with st.popover(
        "설정",
        type="primary" if current in SETTINGS_ITEMS else "secondary",
    ):
        for name in SETTINGS_ITEMS:
            if st.button(name, key=f"navset_{name}", width="stretch"):
                st.session_state["current_view"] = name
                st.query_params["view"] = name
                st.rerun()

# ---- 탭 순서 바꾸기(드래그) ----
# ★위의 '진짜 탭 버튼'은 Streamlit 기본 위젯이라 마우스로 직접 못 끕니다(보안상 컴포넌트가
#   상위 페이지를 못 건드림). 그래서 바로 아래에 '드래그 전용 줄'을 항상 보이게 두고, 여기서
#   탭 이름을 좌우로 끌면 순서가 저장되어 위 탭줄에 바로 반영됩니다. (팝오버 안 열어도 됨)
_show_reorder = st.toggle("🖱 탭 순서 바꾸기", value=False, key="nav_reorder_toggle",
                          help="켜면 아래에 드래그 줄이 나옵니다. 탭을 좌우로 끌어 순서를 바꾸세요(자동 저장).")
if _show_reorder:
    try:
        from streamlit_sortables import sort_items
        st.caption("↔ 아래 탭을 **좌우로 끌어** 순서를 바꾸세요. 바꾸면 자동 저장되고 위 탭줄에 바로 반영됩니다.")
        _new_order = sort_items(_nav_order, direction="horizontal", key="nav_sort_items")
        if _new_order and list(_new_order) != list(_nav_order):
            settings_repository.set_setting("nav_stage_order", "|".join(_new_order))
            st.rerun()
    except Exception:  # noqa: BLE001
        st.caption("탭 순서 편집 도구를 불러오지 못했습니다. (streamlit-sortables 설치 필요)")
    if st.button("기본 순서로 되돌리기", key="nav_order_reset"):
        settings_repository.set_setting("nav_stage_order", "|".join(FLAT_STAGES))
        st.rerun()

st.divider()

# fragment(상세패널·표 버튼) 안에서 예약해 둔 다이얼로그를 메인 문맥에서 엽니다.
# (st.dialog 는 fragment 안에서 직접 호출하면 창이 안 떠서, 여기서 통일 처리)
common.run_pending_dialog()

# ---- 현재 선택된 화면 그리기 ----
VIEWS[current]()
