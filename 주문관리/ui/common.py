# ==========================================================
# 화면 공통 도구 (ui/common.py)
# ----------------------------------------------------------
# 신규주문/발송대기/배송중/배송완료/구매확정 화면이 똑같이 쓰는 표시용
# 함수들을 모아둔 곳입니다. (상품/금액 문자열 만들기, 목록 표 그리기,
# 상세내역 표 그리기 등)
# ==========================================================

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
import io
import os
import subprocess
import time

import pandas as pd
import streamlit as st

import models
from repositories import (
    claims_repository,
    order_repository,
    product_link_repository,
    settings_repository,
)
from services import claims_sync_service, privacy, product_link_service, sync_service
from ui import settings

MARKET_DISPLAY_NAME = {"coupang": "쿠팡"}

# 쿠팡 마켓수수료율(정산 계산용). 판매자 방침으로 12% 일괄 적용합니다.
COUPANG_FEE_RATE = 0.12


# ----------------------------------------------------------
# 빠른 기간 선택 (샵마인 화면의 "기간지정/금일/어제/이번주/N주전/이달/전달/..."
# 드롭다운과 같은 방식입니다)
# ----------------------------------------------------------
QUICK_PERIOD_OPTIONS = [
    "직접 지정", "오늘", "어제",
    "최근 3일", "최근 1주일", "최근 2주일", "최근 1개월", "최근 3개월",
    "이번주", "지난주",
    "1주전", "2주전", "3주전", "4주전", "5주전", "6주전", "7주전", "8주전", "9주전", "10주전",
    "이번달", "전달", "전전달", "올해", "작년", "전전년", "전체",
]


def _week_range(weeks_ago: int):
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(weeks=weeks_ago)
    end = start + timedelta(days=6)
    return start, min(end, today)


def _month_range(months_ago: int):
    today = date.today()
    year = today.year
    month = today.month - months_ago
    while month <= 0:
        month += 12
        year -= 1
    start = date(year, month, 1)
    if months_ago == 0:
        return start, today
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    end = end - timedelta(days=1)
    return start, end


def _year_range(years_ago: int):
    today = date.today()
    year = today.year - years_ago
    start = date(year, 1, 1)
    end = today if years_ago == 0 else date(year, 12, 31)
    return start, end


def _recent_days(n: int):
    """오늘 포함 최근 n일. (예: 최근 3일 = 그저께~오늘)"""
    today = date.today()
    return today - timedelta(days=n - 1), today


def _recent_months(n: int):
    """오늘 기준 최근 n개월. (예: 최근 3개월 = 3개월 전 같은 날~오늘)"""
    import calendar

    today = date.today()
    month = today.month - n
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day), today


def _quick_period_to_range(label: str):
    """빠른 선택 항목을 (시작일, 종료일)로 바꿉니다. '직접 지정'이면 None을 돌려줍니다."""
    if label == "오늘":
        today = date.today()
        return today, today
    if label == "어제":
        yesterday = date.today() - timedelta(days=1)
        return yesterday, yesterday
    if label == "최근 3일":
        return _recent_days(3)
    if label == "최근 1주일":
        return _recent_days(7)
    if label == "최근 2주일":
        return _recent_days(14)
    if label == "최근 1개월":
        return _recent_months(1)
    if label == "최근 3개월":
        return _recent_months(3)
    if label == "이번주":
        return _week_range(0)
    if label == "지난주":
        return _week_range(1)
    if label == "전체":
        return date(2020, 1, 1), date.today()
    if label.endswith("주전"):
        return _week_range(int(label[:-2]))
    if label == "이번달":
        return _month_range(0)
    if label == "전달":
        return _month_range(1)
    if label == "전전달":
        return _month_range(2)
    if label == "올해":
        return _year_range(0)
    if label == "작년":
        return _year_range(1)
    if label == "전전년":
        return _year_range(2)
    return None


def render_period_picker(key_prefix: str, label_from: str, label_to: str, default_days: int = 14):
    """
    "빠른 선택" 드롭다운 + 시작일 + 종료일 입력을 그리고, (period_from, period_to)를
    돌려줍니다. 빠른 선택에서 "이번주"/"전달" 같은 걸 고르면 시작일/종료일 입력이
    자동으로 그 기간으로 채워집니다 ("직접 지정"을 고르면 손으로 직접 바꿀 수 있습니다).
    """
    from_key = f"{key_prefix}_period_from"
    to_key = f"{key_prefix}_period_to"
    quick_key = f"{key_prefix}_quick_period"
    # DB에 저장해 두는 키(앱을 껐다 켜도 그 탭의 빠른선택/기간이 유지되게).
    save_from_key = f"period_from:{key_prefix}"
    save_to_key = f"period_to:{key_prefix}"
    save_quick_key = f"quick_period:{key_prefix}"

    def _parse_date(text):
        try:
            return date.fromisoformat(text)
        except Exception:
            return None

    # ★핵심: 표 안 버튼 클릭 등 'fragment 재실행' 때는, 화면에 안 그려지는 이 날짜/빠른선택
    #   위젯값을 Streamlit이 초기화해 버립니다. 그래서 다음 전체 새로고침에서 날짜가 '풀려' 보입니다.
    #   → 위젯값을 '미러(비-위젯 세션키, fragment 재실행에도 안 사라짐)'에 매번 복사해두고,
    #     위젯값이 사라졌으면 미러에서 되살립니다. (이게 확실한 유지 방법)
    quick_mirror = f"{key_prefix}__quick_mirror"
    from_mirror = f"{key_prefix}__from_mirror"
    to_mirror = f"{key_prefix}__to_mirror"
    inited_key = f"{key_prefix}_period_inited"

    # 빠른 선택 복원: 미러 > 저장값 > 기본
    if quick_key not in st.session_state:
        saved_q = settings_repository.get_setting(save_quick_key)
        st.session_state[quick_key] = (
            st.session_state.get(quick_mirror)
            or (saved_q if saved_q in QUICK_PERIOD_OPTIONS else None)
            or QUICK_PERIOD_OPTIONS[0]
        )
    # 시작일 복원: 미러 > 저장값 > 기본
    if from_key not in st.session_state:
        st.session_state[from_key] = (
            st.session_state.get(from_mirror)
            or _parse_date(settings_repository.get_setting(save_from_key) or "")
            or (date.today() - timedelta(days=default_days))
        )
    # 종료일 복원: 미러 > 저장값 > 오늘(기본). 시작일과 똑같이 '사용자가 정한 값을 계속 유지'.
    #   (요청: 날짜를 '고정'되게. '직접 지정'이면 그 값 유지, '빠른 선택(최근 N일 등)'이면
    #    아래 quick_range 재계산으로 종료일이 자동으로 오늘까지 맞춰집니다.)
    if to_key not in st.session_state:
        st.session_state[to_key] = (
            st.session_state.get(to_mirror)
            or _parse_date(settings_repository.get_setting(save_to_key) or "")
            or date.today()
        )
    st.session_state[inited_key] = True

    col_quick, col_from, col_to = st.columns([1, 1, 1])
    with col_quick:
        quick = st.selectbox("빠른 선택", options=QUICK_PERIOD_OPTIONS, key=quick_key)

    # ⭐ 빠른 선택(최근 N일/오늘/이번주 등)은 '항상 오늘 기준'으로 다시 계산합니다.
    #    (시작/종료일을 손으로 정하려면 빠른 선택을 '직접 지정'으로 두세요)
    quick_range = _quick_period_to_range(quick)
    if quick_range:
        st.session_state[from_key] = quick_range[0]
        st.session_state[to_key] = quick_range[1]

    with col_from:
        period_from = st.date_input(label_from, key=from_key)
    with col_to:
        period_to = st.date_input(label_to, key=to_key)

    # ★미러에 현재값을 매번 복사(비-위젯키 → fragment 재실행에도 유지). 위젯이 초기화돼도 되살림.
    st.session_state[quick_mirror] = quick
    st.session_state[from_mirror] = period_from
    st.session_state[to_mirror] = period_to

    # 사용자가 정한 빠른선택·기간을 DB에 저장 → 앱을 껐다 켜도 그대로 유지됩니다. (바뀔 때만 저장)
    if settings_repository.get_setting(save_quick_key) != quick:
        settings_repository.set_setting(save_quick_key, quick)
    if period_from and settings_repository.get_setting(save_from_key) != period_from.isoformat():
        settings_repository.set_setting(save_from_key, period_from.isoformat())
    if period_to and settings_repository.get_setting(save_to_key) != period_to.isoformat():
        settings_repository.set_setting(save_to_key, period_to.isoformat())

    return period_from, period_to


def effective_period_for(key_prefix: str, default_days: int = 3):
    """
    render_period_picker를 거치지 않고(예: 앱 시작 시 자동수집) 그 탭의 '현재 기간'을 계산합니다.
    저장된 빠른선택이 있으면 오늘 기준으로 다시 계산하고, 없으면 저장된 시작/종료일,
    그것도 없으면 기본(오늘 기준 default_days)을 씁니다.
    """
    quick = settings_repository.get_setting(f"quick_period:{key_prefix}")
    rng = _quick_period_to_range(quick) if quick else None
    if rng:
        return rng

    def _pd(text):
        try:
            return date.fromisoformat(text)
        except Exception:
            return None

    pf = _pd(settings_repository.get_setting(f"period_from:{key_prefix}") or "")
    # 종료일은 항상 오늘(최신). 시작일만 저장값(없으면 기본)을 씁니다.
    return (pf or (date.today() - timedelta(days=default_days - 1)), date.today())

# 사용자가 기존에 쓰던 엑셀 양식("발송대기-(기본)-검색결과...xls", 72개 컬럼)의
# 컬럼을 순서 그대로 나열합니다. 모든 목록 화면(신규주문/발송대기/배송중/배송완료/
# 구매확정)이 같은 형식을 씁니다. 데이터 출처가 확인되지 않은 항목은 지어내지
# 않고 "-"로 비워둡니다 (형식은 유지하되 값은 채우지 않음).
FULL_COLUMNS = [
    "No", "주문상태", "퀵스타", "쇼핑몰", "쇼핑몰ID", "별칭(쇼핑몰계정)", "주문일(약식)",
    "구매자", "수령자", "주문고유코드", "주문번호", "주문순번", "택배사", "송장번호",
    "상품번호", "쿠팡상품번호", "상품명", "선택사항", "수량", "단가", "추가구성",
    "수령자전화번호", "수령자휴대전화", "구매자전화", "구매자휴대전화",
    "우편번호", "주소", "배송메모", "CS메모", "배송구분",
    "총주문금액", "옵션추가금액", "마켓수수료금액", "수수료율", "실수수료율",
    "공급금액", "배송비", "할인금액", "정산예상금액",
    "주문일시", "결제일시", "클레임접수일시", "발송예정일",
    "인터파크 공급계약번호", "배송번호", "주문자ID", "상품URL",
    "판매자상품코드", "판매자옵션코드", "쇼핑몰계정고유번호", "네이버 아이디 로그인",
    "스토어명", "개인통관고유부호", "샵마인주문상태", "송장입력", "보류여부",
    "도서산간", "정산추가정보", "발송일시", "결제수단", "배송방법", "실결제금액",
    "최초 엑셀생성 일시(로컬)", "최근 엑셀생성 일시(로컬)", "송장번호입력일시(로컬)",
    "송장상품수량", "추가구성수량", "톡톡하기", "주문일자(yyyyMMdd)",
    "송장실결제금액", "송장총주문금액", "작업상태", "메모(쇼핑몰계정)",
]

# 엑셀 양식에는 없지만 통관검증 상태를 보여주기 위해 모든 목록 화면에서
# 추가로 쓰는 항목입니다. 표 맨 앞쪽에 붙여서 바로 눈에 띄게 합니다.
VALIDATION_COLUMNS = ["통관검증 상태", "불일치 사유", "마지막 검증일시", "발송 가능 여부"]

# 퀵스타 배대지 접수 결과를 보여주는 항목입니다.
QUICKSTAR_COLUMNS = ["퀵스타 신청번호", "퀵스타 운송장", "퀵스타 접수일시"]

# 표 한 줄의 높이(픽셀). 샵마인처럼 한 화면에 많은 주문이 보이도록 촘촘하게 잡았습니다.
# (Streamlit 기본값은 35px 정도라 여백이 큽니다)
TABLE_ROW_HEIGHT = 26


def format_items(items: list) -> str:
    return ", ".join(item["product_name"] for item in items) or "-"


def format_options(items: list) -> str:
    return ", ".join(item["option_name"] for item in items if item["option_name"]) or "-"


def format_item_ids(items: list) -> str:
    return ", ".join(item["market_item_id"] for item in items if item["market_item_id"]) or "-"


def _invoice_to_str(value) -> str:
    """
    표에서 읽어온 송장번호 값을 안전하게 '문자열'로 바꿉니다.

    송장번호를 숫자로 입력하면 표(pandas/data_editor)가 그 칸을 숫자(float)로
    바꿔버릴 수 있습니다. 그대로 .strip()을 하면 "float에는 strip이 없다"는
    오류가 나고, str()을 하면 "8101855151608.0"처럼 소수점이 붙습니다.
    그래서 여기서 float/None/빈값을 모두 깔끔한 문자열로 정리합니다.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float):
        # 정수형 송장번호가 float로 넘어온 경우: 소수점 없이 정수 문자열로.
        return str(int(value))
    return str(value).strip()


def _format_item_field(items: list, field: str) -> str:
    """상품 항목마다 있는 값(판매자상품코드 등)을, 중복은 빼고 이어붙입니다."""
    values = []
    for item in items:
        value = item.get(field)
        if value and value not in values:
            values.append(value)
    return ", ".join(values) or "-"


def coupang_product_url(vendor_item_id: str, product_id: str = None, item_id: str = None) -> str:
    """
    쿠팡 상품 페이지 주소를 만듭니다.

    쿠팡 상품 페이지의 정확한 주소는
        https://www.coupang.com/vp/products/{productId}?itemId={itemId}&vendorItemId={vendorItemId}
    형태입니다. productId(노출상품ID)를 알면 이 형태로 만들어 오류 없이 열립니다.

    productId를 아직 모르면(상품조회 전) 임시로 vendorItemId만으로 만듭니다.
    이 임시 주소도 해당 상품으로 연결은 되지만, 경로의 상품ID가 맞지 않아
    페이지에서 "서버 오류" 안내창이 뜰 수 있습니다. 상세보기를 한 번 열면
    productId가 조회·저장되어 그다음부터는 정확한 주소가 만들어집니다.
    """
    if not vendor_item_id:
        return ""
    if product_id:
        return (
            f"https://www.coupang.com/vp/products/{product_id}"
            f"?itemId={item_id or ''}&vendorItemId={vendor_item_id}"
        )
    return f"https://www.coupang.com/vp/products/{vendor_item_id}?vendorItemId={vendor_item_id}"


def _item_link_info(item: dict) -> dict:
    """
    상품 항목 하나에 대해 캐시된 productId/itemId를 붙여서 돌려줍니다.
    돌려주는 값: {"vendor_item_id", "product_id", "item_id", "url", "코드표시"}
      - 코드표시: 쿠팡 상품 페이지에 보이는 "쿠팡상품번호" (productId - itemId)
    """
    vendor_item_id = item.get("market_item_id") or ""
    cached = product_link_repository.get(vendor_item_id) if vendor_item_id else None
    product_id = (cached or {}).get("product_id") or ""
    item_id = (cached or {}).get("item_id") or ""
    url = coupang_product_url(vendor_item_id, product_id, item_id)
    if product_id:
        code_display = f"{product_id} - {item_id}" if item_id else product_id
    else:
        code_display = ""  # 아직 조회 전
    return {
        "vendor_item_id": vendor_item_id,
        "product_id": product_id,
        "item_id": item_id,
        "url": url,
        "코드표시": code_display,
    }


def _product_urls(items: list) -> list:
    """주문에 담긴 상품들의 쿠팡 상품 페이지 주소 목록(중복 제외)을 돌려줍니다."""
    urls = []
    for item in items:
        url = _item_link_info(item)["url"]
        if url and url not in urls:
            urls.append(url)
    return urls


def _coupang_product_codes(items: list) -> str:
    """
    쿠팡 상품 페이지에 표시되는 "쿠팡상품번호"(productId - itemId)들을 이어붙입니다.
    아직 조회 전이면 "-"를 돌려줍니다.
    """
    codes = []
    for item in items:
        code = _item_link_info(item)["코드표시"]
        if code and code not in codes:
            codes.append(code)
    return ", ".join(codes) or "-"


def _find_chrome() -> str:
    """이 컴퓨터에 설치된 크롬 실행 파일 경로를 찾습니다. 없으면 None."""
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def open_in_chrome(url: str) -> bool:
    """
    주어진 주소를 (인앱 브라우저가 아니라) 크롬에서 엽니다.
    이 프로그램은 사용자 컴퓨터에서 직접 도는 데스크톱 앱이라, 여기서 크롬을
    실행하면 사용자 화면에 크롬 창이 뜹니다. 크롬이 없으면 False를 돌려줍니다.
    """
    chrome = _find_chrome()
    if not chrome or not url:
        return False
    subprocess.Popen([chrome, url])
    return True


def format_quantity(items: list) -> int:
    return sum(item["quantity"] or 0 for item in items)


def format_sales_amount(items: list) -> int:
    # sales_amount는 이미 상품줄 전체 금액입니다 (수량을 다시 곱하지 않습니다).
    return sum(item["sales_amount"] or 0 for item in items)


def order_totals(orders: list) -> dict:
    """
    현재 목록의 합계(건수/송장/상품/금액)를 계산합니다. 정산금액은 쿠팡 매출내역으로 가져온
    '실제 정산금액'(order['settlement_amount'])이 있으면 그걸, 없으면 추정(수수료 12%)을 씁니다.
    """
    count = len(orders)
    qty = sales = fee = settlement = shipping = 0
    boxes = set()
    for order in orders:
        items = order.get("items") or []
        s = format_sales_amount(items)
        real = order.get("settlement_amount")
        if real is not None:
            settle = int(real)
            f = max(s - settle, 0)
        else:
            f = round(s * COUPANG_FEE_RATE)
            settle = s - f
        sales += s
        fee += f
        settlement += settle
        qty += format_quantity(items)
        shipping += order.get("shipping_fee") or 0
        if order.get("shipment_box_id"):
            boxes.add(order["shipment_box_id"])
    rate = round(fee / sales * 100, 1) if sales else 0.0
    return {
        "건수": count, "송장": len(boxes) or count, "상품": qty,
        "총주문금액": sales, "마켓수수료금액": fee, "수수료율": rate,
        "공급금액": sales - fee, "할인금액": 0, "실결제금액": sales,
        "정산예상금액": settlement, "배송비": shipping, "정산예정금액_배송포함": settlement + shipping,
    }


def render_order_summary_bar(orders: list) -> None:
    """샵마인 하단 상태바처럼, 현재 목록의 합계를 한 줄로 보여줍니다."""
    t = order_totals(orders)

    def _won(value):
        return f"{int(value):,}원"

    sep = '<span style="opacity:.35;">|</span>'
    parts = [
        f"총 <b style='font-weight:600;'>{t['건수']}</b>건",
        f"송장 <b style='font-weight:600;'>{t['송장']}</b>장",
        f"상품 <b style='font-weight:600;'>{t['상품']}</b>개",
        f"결제 <b style='font-weight:600;'>{_won(t['총주문금액'])}</b>",
        f"수수료 <b style='font-weight:600;'>{_won(t['마켓수수료금액'])}</b>",
        f"정산 <b style='font-weight:600;'>{_won(t['정산예상금액'])}</b>",
        f"정산+배송 <b style='font-weight:600;'>{_won(t['정산예정금액_배송포함'])}</b>",
    ]
    inner = f" {sep} ".join(parts)
    st.markdown(
        f'<div style="display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;'
        f'background:var(--secondary-background-color,#f0f2f6);'
        f'border:0.5px solid var(--border,rgba(49,51,63,0.15));border-radius:8px;'
        f'padding:8px 14px;font-size:13px;color:var(--text-color,#31333F);">{inner}</div>',
        unsafe_allow_html=True,
    )


# ==========================================================
# 공용: fragment 안에서 다이얼로그(st.dialog) 열기
# ----------------------------------------------------------
# st.dialog 는 @st.fragment 안(상세패널·표 버튼 콜백 등)에서 직접 호출하면 '창이 안 뜹니다'.
# 그래서 fragment 안에서는 open_dialog_deferred()로 '예약'만 하고 전체 새로고침 →
# app.py 가 매 실행 앞에서 run_pending_dialog()로 메인 스크립트 문맥에서 엽니다.
# (이 패턴을 쓰면 어느 화면에서든 다이얼로그가 안 뜨는 문제가 재발하지 않습니다.)
# ==========================================================

def open_dialog_deferred(dialog_fn, *args, **kwargs) -> None:
    """fragment 안에서 다이얼로그를 열 때 사용. 예약해두고 '전체' 새로고침합니다.
    ★scope='app' 필수: fragment 안에서 그냥 st.rerun()하면 fragment만 다시 돌아
      app.py의 run_pending_dialog()가 실행되지 않아 창이 안 뜹니다."""
    st.session_state["_pending_dialog"] = (dialog_fn, args, kwargs)
    # 다이얼로그가 떠 있는 동안 배경(수집 진행 fragment 등)의 전체 새로고침이 창을 닫지
    # 않도록 억제 플래그를 켭니다. (닫히면 run_pending_dialog가 자동으로 끕니다)
    st.session_state["_suppress_bg_rerun"] = True
    st.rerun(scope="app")


def run_pending_dialog() -> None:
    """메인 스크립트(app.py)에서 매 실행 앞부분에 호출. 예약된 다이얼로그가 있으면 엽니다."""
    pending = st.session_state.pop("_pending_dialog", None)
    if pending:
        fn, args, kwargs = pending
        fn(*args, **kwargs)
    else:
        # 이번 실행에 새로 열 다이얼로그가 없음 = 닫혔거나 없음 → 배경 새로고침 억제 해제.
        st.session_state.pop("_suppress_bg_rerun", None)

    # ★긴 작업 버튼(송장 자동입력 등)의 on_click이 걸어둔 '수명(ttl)' 억제를 매 실행마다
    #   1씩 줄입니다. app.py 맨 앞에서 도는데, ttl=2로 걸면 '클릭이 처리되는 이번 실행'과
    #   '그 핸들러의 후속 rerun'까지 억제가 유지돼(=이번 실행 내내 fragment가 preempt 못함),
    #   그 다음 실행에서 0이 되어 정상으로 풀립니다.
    ttl = int(st.session_state.get("_suppress_bg_ttl", 0) or 0)
    if ttl > 0:
        st.session_state["_suppress_bg_ttl"] = ttl - 1
    else:
        st.session_state.pop("_suppress_bg_ttl", None)


def arm_bg_rerun_suppression() -> None:
    """긴 작업 버튼의 on_click에서 호출. 다음 실행(클릭 처리)에서 배경수집 fragment가
    핸들러를 중간에 끊지 못하도록 억제 수명(ttl)을 켭니다."""
    st.session_state["_suppress_bg_ttl"] = 2


def bg_rerun_suppressed() -> bool:
    """배경 전체 새로고침(자동수집 완료 시 rerun)을 잠시 미뤄야 하는지.
    다이얼로그가 떠 있거나(_suppress_bg_rerun), 긴 작업이 진행 중(_suppress_bg_ttl>0)일 때."""
    if st.session_state.get("_suppress_bg_rerun"):
        return True
    return int(st.session_state.get("_suppress_bg_ttl", 0) or 0) > 0


from contextlib import contextmanager


@contextmanager
def suppress_bg_rerun():
    """★긴 작업(송장 자동입력·GR 채우기 등) 실행 중에는 배경 자동수집 fragment가
    완료 시점에 `st.rerun(scope='app')`으로 **실행 중인 핸들러를 중간에 끊어버리는** 것을
    막습니다. (이 경쟁조건 때문에 '송장 자동입력'이 가송장을 저장하기 전에 끊겨 송장번호가
    '-'로 남던 문제) with 블록이 끝나면 억제를 자동 해제합니다."""
    prev = st.session_state.get("_suppress_bg_rerun")
    st.session_state["_suppress_bg_rerun"] = True
    try:
        yield
    finally:
        if prev:
            st.session_state["_suppress_bg_rerun"] = prev
        else:
            st.session_state.pop("_suppress_bg_rerun", None)


@st.dialog("통계 — 합계정산내역", width="large")
def stats_dialog(orders: list) -> None:
    """샵마인 '통계 → 합계정산내역'처럼, 현재 목록의 합계 정산내역을 보여줍니다(복사용 텍스트 포함)."""
    t = order_totals(orders)

    def _won(value):
        return f"{int(value):,}원"

    st.caption(
        "현재 목록(검색 반영) 기준 합계입니다. 정산금액은 '쿠팡 실정산'이 반영된 주문은 실제값, "
        "아직 매출인식 전 주문은 추정(수수료 12% 가정)입니다. 정확한 금액은 구매확정 후 쿠팡 윙에서 확인하세요."
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("주문건수", f"{t['건수']}건")
    c2.metric("송장수", f"{t['송장']}장")
    c3.metric("상품수", f"{t['상품']}개")

    rows = [
        ("총주문금액", _won(t["총주문금액"])),
        ("마켓수수료금액", _won(t["마켓수수료금액"])),
        ("수수료율", f"{t['수수료율']}%"),
        ("공급금액", _won(t["공급금액"])),
        ("할인금액", _won(t["할인금액"])),
        ("실결제금액", _won(t["실결제금액"])),
        ("정산예상금액", _won(t["정산예상금액"])),
        ("배송비", _won(t["배송비"])),
        ("정산예정금액(배송비포함)", _won(t["정산예정금액_배송포함"])),
    ]
    st.table(pd.DataFrame(rows, columns=["항목", "금액"]).set_index("항목"))

    st.markdown("**정보텍스트(복사용)**")
    lines = [f"주문건수: {t['건수']}건", f"송장수: {t['송장']}장", f"상품수: {t['상품']}개"]
    lines += [f"{k}: {v}" for k, v in rows]
    st.code("\n".join(lines))
    if st.button("닫기", width="stretch"):
        st.rerun()


def matches_search(row: dict, keyword: str) -> bool:
    keyword = keyword.strip().lower()
    if not keyword:
        return True
    return any(keyword in str(value).lower() for value in row.values())


def render_live_count_banner(work_status: str) -> None:
    """
    "지금 쿠팡에 실제로 몇 건 있는지"를 눈에 띄게 보여줍니다. 우리 DB에
    누적된 전체 건수(상단 탭에 표시되는 숫자)와는 다른 값입니다 - 이건
    마지막으로 "수집하기"를 눌렀을 때 쿠팡이 실시간으로 돌려준 건수입니다.
    """
    last_sync_at = sync_service.get_last_sync_at(work_status)
    last_count = sync_service.get_last_fetched_count(work_status)

    if last_sync_at is None:
        st.caption("아직 한 번도 수집하지 않았습니다.")
        return

    if last_count is not None:
        st.info(f"**쿠팡 기준 현재 {work_status}: {last_count}건** (확인 시각: {last_sync_at})")
    else:
        st.caption(f"마지막 수집 시각: {last_sync_at}")


def run_jobs_with_progress(jobs, label="수집 중", max_workers=2):
    """
    (이름, 실행함수) 목록을 실행하면서 진행률(%)과 남은 시간을 진행바로 보여줍니다.
    실행함수는 화면(st.*)을 건드리지 않는 순수 작업이어야 합니다(다른 스레드에서 돕니다).
    돌려주는 값: [(이름, 결과), ...] (원래 순서 그대로)
    """
    total = len(jobs)
    if total == 0:
        return []
    bar = st.progress(0.0, text=f"{label}… 0/{total} (0%)")
    results = [None] * total
    start = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(fn): (idx, name) for idx, (name, fn) in enumerate(jobs)
        }
        for future in as_completed(future_to_index):
            idx, name = future_to_index[future]
            try:
                results[idx] = (name, future.result())
            except Exception as error:
                results[idx] = (name, {"status": "fail", "error_message": str(error)})
            done += 1
            elapsed = time.time() - start
            remaining = int((elapsed / done) * (total - done)) if done else 0
            pct = int(done / total * 100)
            bar.progress(
                done / total,
                text=f"{label}… {done}/{total} ({pct}%) · 약 {remaining}초 남음  [방금: {name}]",
            )
    bar.progress(1.0, text=f"{label} 완료 · {total}/{total} (100%)")
    time.sleep(0.4)  # 100% 완료 표시를 잠깐 보여준 뒤 정리
    bar.empty()
    return results


def collect_all_stages_with_progress(period_from, period_to) -> dict:
    """
    주문 4단계(신규주문/발송대기/배송중/배송완료)를 진행바를 보여주며 한꺼번에 수집합니다.
    각 화면의 '수집하기'가 이 함수를 써서, 그 화면만이 아니라 전체 단계를 최신화합니다.
    (예: 발송대기였던 주문이 배송중으로 넘어갔으면, 수집 후 발송대기에서 빠지고 배송중으로 이동)
    """
    # 속도 개선: 예전엔 단계별로 4번 가져오고 '사라진 주문 정리'가 또 전체를
    # 가져와서 중복이 컸습니다. 이제 '계정별로 한 번에 다 가져오고 정리까지' 하고,
    # 계정들을 병렬로 돌립니다(가져오는 양 절반 + 병렬).
    accounts = sync_service.coupang_accounts()
    if not accounts:
        return {"status": "fail", "fetched_count": 0, "new_count": 0, "updated_count": 0,
                "closed_count": 0, "error_message": "등록된 쿠팡 계정이 없습니다."}

    jobs = [
        (acc["market_name"], (lambda a=acc: sync_service.sync_and_reconcile_account(a, period_from, period_to)))
        for acc in accounts
    ]
    results = run_jobs_with_progress(jobs, label="주문 최신화 중", max_workers=min(len(jobs), 4))

    per_stage_totals = {}
    total_new = total_updated = closed_count = 0
    errors = []
    for name, result in results:
        for stage, cnt in (result.get("per_stage") or {}).items():
            per_stage_totals[stage] = per_stage_totals.get(stage, 0) + cnt
        total_new += result.get("new", 0) or 0
        total_updated += result.get("updated", 0) or 0
        closed_count += result.get("closed", 0) or 0
        errors.extend(result.get("errors") or [])
        if result.get("status") == "fail" and result.get("error_message"):
            errors.append(f"[{name}] {result['error_message']}")

    # 단계별 실시간 건수 저장(각 화면 상단 배너용)
    sync_service.record_stage_live_counts(per_stage_totals)
    total_fetched = sum(per_stage_totals.values())

    if not errors:
        status = "success"
    elif total_fetched > 0 or total_new + total_updated > 0 or closed_count > 0:
        status = "partial"
    else:
        status = "fail"

    return {
        "status": status,
        "fetched_count": total_fetched,
        "new_count": total_new,
        "updated_count": total_updated,
        "closed_count": closed_count,
        "error_message": " / ".join(errors[:6]) if errors else None,
    }


@st.fragment(run_every=1.0)
def _collect_progress_fragment(key: str) -> None:
    """
    이 키(단계)의 수집이 백그라운드로 도는 동안 1초마다 진행상황을 폴링해 진행바로
    보여줍니다. 끝나면 전체 화면을 새로고침해 결과 배너 + 최신 목록이 나타나게 합니다.
    """
    from services import collect_worker

    state = collect_worker.get_state(key)
    if state["status"] == "running":
        st.progress(min(max(state["progress"], 0.0), 1.0), text=state["progress_text"] or "수집 중…")
        st.caption("⏳ 다른 메뉴로 옮겨도 수집은 백그라운드에서 계속 진행됩니다. 이 화면으로 돌아오면 결과가 반영됩니다.")
    elif bg_rerun_suppressed():
        # 다이얼로그(팝업)가 떠 있는 동안엔 전체 새로고침을 미룹니다(창이 닫히지 않게).
        st.caption("✅ 수집이 끝났습니다. 열린 창을 닫으면 결과가 반영됩니다.")
    else:
        # 완료됨 → 전체 화면을 새로고침해 결과(배너+최신 목록)를 보여줍니다.
        st.rerun(scope="app")


def render_background_collect(period_from, period_to, key: str, stages: list = None,
                              reconcile: bool = False, label: str = "수집하기",
                              advance_delivered: bool = False,
                              advance_confirmed: bool = False) -> None:
    """
    '수집하기'를 '백그라운드 스레드'로 돌리는 공용 버튼+진행표시입니다.

    ⭐ 단계별 동시 수집: 상태를 버튼 키(key)별로 따로 관리하므로, 발송대기에서
    수집 중에도 신규주문·배송중에서 각각 수집을 '동시에' 누를 수 있습니다.
    각 수집은 지정한 단계(stages)만 최신화합니다.
      - stages: 이 화면에서 수집할 작업단계 목록(예: [WORK_STATUS_NEW]). None이면 안내만.
      - reconcile: 신규주문·발송대기 화면에서 True(취소·반품으로 사라진 주문 정리).
    화면을 옮겨도 수집은 collect_worker(별도 스레드)가 끝까지 진행합니다.
    """
    from services import collect_worker

    if not stages:
        return

    state = collect_worker.get_state(key)

    # 1) 방금 끝난 수집 결과가 있으면 먼저 배너로 보여주고 상태를 정리합니다.
    if state["status"] == "done":
        result = state["result"] or {}
        collect_worker.clear(key)
        if result.get("status") == "fail":
            st.error(f"주문 수집 실패: {result.get('error_message')}")
        else:
            closed = result.get("closed_count") or 0
            advanced = result.get("advanced_count") or 0
            message = (
                f"수집 완료 "
                f"(신규 {result.get('new_count', 0)}건, 갱신 {result.get('updated_count', 0)}건"
                + (f", 다음단계 이동 {advanced}건" if advanced else "")
                + (f", 취소·반품 정리 {closed}건" if closed else "")
                + ")"
            )
            if result.get("error_message"):
                st.warning(message + f"\n일부 오류: {result['error_message']}")
            else:
                st.success(message)
        state = collect_worker.get_state(key)  # 이제 idle

    running = state["status"] == "running"

    # 2) 수집하기 버튼(이 단계가 돌고 있을 때만 이 버튼이 비활성 — 다른 단계는 영향 없음)
    clicked = st.button(label, type="primary", width="stretch", key=f"{key}_collect", disabled=running)
    if clicked and not running:
        # ★수집은 표시 필터와 무관하게 '항상 오늘까지' 가져옵니다(새로 생긴 건 안 놓치게).
        collect_to = date.today()
        collect_from = min(period_from, collect_to)
        collect_worker.start(key, stages, collect_from, collect_to, reconcile=reconcile,
                             advance_delivered=advance_delivered,
                             advance_confirmed=advance_confirmed)
        st.rerun()

    # 3) 돌고 있으면 진행바(1초마다 폴링). 끝나면 프래그먼트가 전체 새로고침.
    if running:
        _collect_progress_fragment(key)


def build_excel_bytes(rows: list) -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame(rows).to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


# 사용자 정의 엑셀 양식에서 컬럼 '값'으로 고를 수 있는 특수 항목(주문 컬럼이 아닌 것).
EXCEL_SPECIAL_VALUES = ["(빈칸)", "(오늘 날짜)"]
# FULL_COLUMNS(표 컬럼)에는 없지만 엑셀 양식 값으로 고를 수 있는 계산 항목(배송비 포함 금액 등).
# build_full_row가 이 키들도 채워주므로 엑셀 생성 시 값이 들어갑니다.
EXCEL_COMPUTED_VALUES = ["실결제금액(배송비포함)", "정산예정금액(배송비포함)"]


def excel_value_options() -> list:
    """엑셀 양식 만들 때 각 컬럼의 '값'으로 고를 수 있는 항목 목록(특수값 + 계산항목 + 모든 주문 컬럼)."""
    all_columns = ["No"] + VALIDATION_COLUMNS + QUICKSTAR_COLUMNS + [col for col in FULL_COLUMNS if col != "No"]
    return EXCEL_SPECIAL_VALUES + EXCEL_COMPUTED_VALUES + all_columns


def build_excel_from_template(full_rows: list, template_columns: list) -> bytes:
    """
    사용자 양식(template_columns=[{"header":.., "value":..}, ...])에 맞춰 엑셀을 만듭니다.
    컬럼 순서 = template_columns 순서, 제목 = header, 값 = 각 주문의 value 컬럼(또는 특수값).
    """
    today = date.today().isoformat()
    headers = [(col.get("header") or f"열{i + 1}") for i, col in enumerate(template_columns)]
    data = []
    for row in full_rows:
        line = []
        for col in template_columns:
            value_field = col.get("value")
            if value_field == "(오늘 날짜)":
                line.append(today)
            elif value_field in (None, "", "(빈칸)"):
                line.append("")
            else:
                line.append(row.get(value_field, ""))
        data.append(line)
    buffer = io.BytesIO()
    # columns=headers 로 넘기면 제목이 중복돼도(양식에서 같은 제목 두 번 써도) 안전합니다.
    pd.DataFrame(data, columns=headers).to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


def render_template_excel_download(excel_rows: list, key: str, filename: str) -> None:
    """저장된 사용자 엑셀 양식을 골라, 그 형식대로 엑셀을 다운로드하는 UI."""
    from repositories import excel_template_repository

    templates = excel_template_repository.list_templates()
    if not templates:
        st.caption("사용자 엑셀 양식이 없습니다. (설정 → 엑셀 양식에서 만들 수 있어요)")
        return
    col_pick, col_dl = st.columns([2, 1])
    with col_pick:
        names = [t["name"] for t in templates]
        pick = st.selectbox(
            "엑셀 양식", range(len(names)), format_func=lambda i: names[i], key=f"{key}_tpl_pick"
        )
    template = templates[pick]
    with col_dl:
        st.write("")
        st.download_button(
            "📋 양식대로 다운로드",
            data=build_excel_from_template(excel_rows, template["columns"]),
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
            key=f"{key}_tpl_dl",
        )


@st.dialog("엑셀 파일 생성", width="large")
def excel_export_dialog(all_orders: list, selected_orders: list, key: str, reveal: bool = True) -> None:
    """
    샵마인 '엑셀파일생성' 팝업처럼, 생성 대상(검색결과 전체/선택 주문만)과 엑셀 양식을 골라
    엑셀을 생성(다운로드)합니다. 양식은 '설정 → 엑셀 양식'에서 만든 것 + 기본 전체 컬럼.
    """
    from repositories import excel_template_repository

    st.caption("생성 대상과 엑셀 양식을 고른 뒤 '엑셀 파일 생성'을 누르면 다운로드됩니다.")
    n_all, n_sel = len(all_orders), len(selected_orders)
    target = st.radio(
        "엑셀 생성 대상",
        [f"검색 결과 전체 ({n_all}건)", f"선택한 주문만 ({n_sel}건)"],
        horizontal=True, key=f"{key}_exp_target",
    )
    orders = all_orders if target.startswith("검색") else selected_orders

    templates = excel_template_repository.list_templates()
    names = ["(기본 전체 컬럼)"] + [t["name"] for t in templates]
    pick = st.selectbox("엑셀 양식", range(len(names)), format_func=lambda i: names[i], key=f"{key}_exp_tpl")

    rows = [build_full_row(o, i + 1, reveal=reveal) for i, o in enumerate(orders)]
    data = build_excel_bytes(rows) if pick == 0 else build_excel_from_template(rows, templates[pick - 1]["columns"])

    st.caption(f"생성 대상 **{len(orders)}건** · 양식 **{names[pick]}**")
    col_dl, col_close = st.columns(2)
    with col_dl:
        st.download_button(
            "📥 엑셀 파일 생성", data=data, file_name=f"{key}_주문.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch", type="primary", key=f"{key}_exp_dl", disabled=not orders,
        )
    with col_close:
        if st.button("닫기", width="stretch", key=f"{key}_exp_close"):
            st.rerun()


@st.dialog("표시 항목 설정", width="large")
def column_config_dialog(key: str) -> None:
    """
    샵마인 '표시항목설정'처럼, 표에 보여줄 컬럼과 그 순서를 골라 저장합니다.
    저장 위치는 표(render_full_table)가 읽는 것과 같은 설정(column_order:{key})이라,
    여기서 바꾸면 표에 바로 반영되고 프로그램을 껐다 켜도 유지됩니다.
    """
    all_columns = ["No"] + VALIDATION_COLUMNS + QUICKSTAR_COLUMNS + [c for c in FULL_COLUMNS if c != "No"]
    saved_text = settings_repository.get_setting(f"column_order:{key}")
    saved_columns = [c for c in saved_text.split("|") if c in all_columns] if saved_text else all_columns

    st.caption("보고 싶은 항목만 골라서, 고른 순서대로 표에 나옵니다. 여기서 정한 순서는 저장되어 프로그램을 다시 켜도 그대로 유지됩니다.")
    picked = st.multiselect(
        "표시할 항목 (고른 순서대로 표시)",
        options=all_columns, default=saved_columns, key=f"{key}_colcfg_ms",
    )

    def _clear_widget_state():
        # 인라인 expander(같은 설정을 쓰는)와 이 다이얼로그의 위젯 값을 비워, 저장값을 다시 읽게 함
        st.session_state.pop(f"{key}_column_order", None)
        st.session_state.pop(f"{key}_colcfg_ms", None)

    c_save, c_reset, c_close = st.columns(3)
    with c_save:
        if st.button("💾 저장", type="primary", width="stretch", key=f"{key}_colcfg_save"):
            settings_repository.set_setting(f"column_order:{key}", "|".join(picked or all_columns))
            _clear_widget_state()
            st.rerun()
    with c_reset:
        if st.button("기본값으로", width="stretch", key=f"{key}_colcfg_reset"):
            settings_repository.set_setting(f"column_order:{key}", "|".join(all_columns))
            _clear_widget_state()
            st.rerun()
    with c_close:
        if st.button("닫기", width="stretch", key=f"{key}_colcfg_close"):
            st.rerun()


@st.dialog("바꾸기 설정", width="large")
def replace_rules_dialog() -> None:
    """
    샵마인 '바꾸기설정'처럼, 표·엑셀에 나오는 값을 자동으로 바꿔주는 규칙을 만듭니다.
    예) 상품명에 들어간 특정 단어 지우기, 배송메시지 문구 통일 등.
    규칙은 앱 전체에 저장되어 모든 목록·엑셀에 똑같이 적용되고, 재시작해도 유지됩니다.
    """
    import pandas as pd

    all_columns = ["(모든 컬럼)"] + [c for c in FULL_COLUMNS if c != "No"]
    rules = get_replace_rules()

    st.caption(
        "'대상 컬럼'의 값에서 '찾을 값'을 '바꿀 값'으로 바꿉니다. "
        "방식이 '부분'이면 값 안에 든 글자만 바꾸고, '전체'면 값이 정확히 같을 때만 통째로 바꿉니다. "
        "행을 추가·삭제한 뒤 아래 '저장'을 누르세요."
    )

    base = pd.DataFrame(rules or [], columns=["col", "find", "replace", "mode"])
    if base.empty:
        base = pd.DataFrame([{"col": "(모든 컬럼)", "find": "", "replace": "", "mode": "부분"}])
    # 표시용 한글 헤더로 바꿔서 편집기에 보여줍니다.
    base = base.rename(columns={"col": "대상 컬럼", "find": "찾을 값", "replace": "바꿀 값", "mode": "방식"})

    edited = st.data_editor(
        base, num_rows="dynamic", width="stretch", key="replace_rules_editor",
        column_config={
            "대상 컬럼": st.column_config.SelectboxColumn("대상 컬럼", options=all_columns, required=True, width="medium"),
            "찾을 값": st.column_config.TextColumn("찾을 값", width="medium"),
            "바꿀 값": st.column_config.TextColumn("바꿀 값", width="medium"),
            "방식": st.column_config.SelectboxColumn("방식", options=["부분", "전체"], required=True, width="small"),
        },
    )

    c_save, c_close = st.columns(2)
    with c_save:
        if st.button("💾 저장", type="primary", width="stretch", key="replace_rules_save"):
            out = []
            for _, r in edited.iterrows():
                find = str(r.get("찾을 값") or "").strip()
                if not find:
                    continue  # 찾을 값이 빈 행은 저장하지 않습니다.
                out.append({
                    "col": r.get("대상 컬럼") or "(모든 컬럼)",
                    "find": find,
                    "replace": "" if r.get("바꿀 값") is None else str(r.get("바꿀 값")),
                    "mode": r.get("방식") or "부분",
                })
            set_replace_rules(out)
            st.session_state.pop("replace_rules_editor", None)
            st.toast(f"바꾸기 규칙 {len(out)}개를 저장했습니다.", icon="✅")
            st.rerun()
    with c_close:
        if st.button("닫기", width="stretch", key="replace_rules_close"):
            st.rerun()


def _bulk_todo(name: str, selected: list) -> None:
    """
    아직 기능 연결 안 한 샵마인식 버튼(주문서인쇄/작업상태지정/보류처리 등)을 눌렀을 때
    안내합니다. 이 버튼들은 나중에 '선택한 주문 일괄처리'로 연결할 예정이라, 지금은
    선택 건수를 함께 안내해 둡니다.
    """
    n = len(selected or [])
    if n:
        st.toast(f"'{name}'은(는) 곧 선택한 {n}건 일괄처리로 연결할 예정입니다.", icon="🛠")
    else:
        st.toast(f"'{name}'은(는) 곧 일괄처리 기능을 연결할 예정입니다. (표에서 주문을 먼저 선택하세요)", icon="🛠")


def render_shopmine_action_bar(key: str, orders: list, primary_label: str = None,
                               primary_help: str = None) -> bool:
    """
    샵마인식 액션 버튼 바(신규주문에서 쓰던 것)를 공용화한 것입니다. 각 단계 화면이
    같은 모양으로 씁니다.
      - primary_label: 맨 앞 주 버튼 라벨(신규주문='📦 주문확인', 발송대기='🚚 발송처리').
        None이면 주 버튼 없음(예: 배송중/배송완료/구매확정).
      - orders: 지금 목록(검색 반영). 통계·엑셀생성이 이 목록을 씁니다.
    돌려주는 값: 주 버튼(primary)이 눌렸는지 여부(bool). 실제 처리는 화면(호출부)이
    단계에 맞게 합니다. 나머지 유틸 버튼(통계·표시항목·바꾸기·엑셀)은 여기서 처리하고,
    아직 미구현 버튼(주문서인쇄/작업상태지정/보류처리/보류해제/추가기능)은 안내만 합니다.
    """
    selected = st.session_state.get(f"{key}_selected_orders", []) or []
    with st.container(horizontal=True):
        primary_clicked = False
        if primary_label:
            primary_clicked = st.button(primary_label, key=f"{key}_bar_primary", type="primary", help=primary_help)
        _stat = st.button("📊 통계", key=f"{key}_bar_stat")
        _disp = st.button("🗂 표시항목설정", key=f"{key}_bar_disp")
        _swap = st.button("🔧 바꾸기설정", key=f"{key}_bar_swap")
        _print = st.button("🖨 주문서인쇄", key=f"{key}_bar_print")
        _work = st.button("🏷 작업상태지정", key=f"{key}_bar_work")
        _hold = st.button("⏸ 보류처리", key=f"{key}_bar_hold")
        _unhold = st.button("▶ 보류해제", key=f"{key}_bar_unhold")
        _extra = st.button("➕ 추가기능", key=f"{key}_bar_extra")
        _tpl = st.button("📑 엑셀양식설정", key=f"{key}_bar_tplset", help="엑셀 양식 만들기 화면으로 이동합니다.")
        _excel = st.button("📥 엑셀파일생성", key=f"{key}_bar_excelgen", type="primary")
    if _tpl:
        st.session_state["current_view"] = "엑셀 양식"
        st.query_params["view"] = "엑셀 양식"
        st.rerun()
    if _disp:
        column_config_dialog(key)
    if _swap:
        replace_rules_dialog()
    if _stat:
        stats_dialog(orders)
    if _excel:
        excel_export_dialog(orders, selected, key=key)
    for _clicked, _name in [
        (_print, "주문서인쇄"), (_work, "작업상태지정"),
        (_hold, "보류처리"), (_unhold, "보류해제"), (_extra, "추가기능"),
    ]:
        if _clicked:
            _bulk_todo(_name, selected)
    return primary_clicked


def _short_date(ordered_at: str) -> str:
    """주문일시에서 날짜만 뽑아 약식으로 보여줍니다. (예: 2026-07-19)"""
    return (ordered_at or "")[:10] or "-"


def _yyyymmdd(ordered_at: str) -> str:
    try:
        return datetime.fromisoformat(ordered_at).strftime("%Y%m%d")
    except (TypeError, ValueError):
        return "-"


def build_full_row(order: dict, row_no: int, reveal: bool = False) -> dict:
    """
    엑셀 양식(FULL_COLUMNS, 72개 항목) + 통관검증 관련 4개 항목을 한 주문에
    대해 채운 딕셔너리를 돌려줍니다. 신규주문/발송대기/배송중/배송완료/
    구매확정 화면이 전부 이 함수로 목록 표를 만듭니다.
    """
    shipping = order["shipping"] or {}
    items = order["items"]
    validation_status = shipping.get("validation_status")

    # 쿠팡은 수령자 전화번호를 "안심번호"(0502...)로만 주고, 실제 휴대폰번호(010...)는
    # 해외배송 통관정보(customs_phone)에만 들어있습니다. 그래서 실제 번호를 우선 씁니다.
    customs_phone = shipping.get("customs_phone") or ""
    phone = customs_phone or shipping.get("receiver_phone_raw") or ""
    safe_phone = shipping.get("receiver_phone_raw") or ""
    pccc = shipping.get("pccc") or ""
    orderer_phone = order.get("orderer_phone") or ""
    address = f"{shipping.get('address_basic') or '-'} {shipping.get('address_detail') or ''}".strip()
    sales_total = format_sales_amount(items)

    # 정산금액: 쿠팡 매출내역 조회로 가져온 '실제 정산금액'(settlement_amount)이 있으면 그걸
    # 그대로 씁니다. 없으면(신규·발송대기 등 아직 매출인식 전) 추정치(판매금액 × 88%, 수수료
    # 12% 가정)를 씁니다. 실제 수수료율은 상품마다 달라서 추정은 근사값입니다.
    real_settlement = order.get("settlement_amount")
    if real_settlement is not None:
        settlement_estimate = int(real_settlement)
        market_fee = max(sales_total - settlement_estimate, 0)  # 실제 수수료(대략) = 판매 − 정산
        settlement_source = "쿠팡 실정산"
    else:
        market_fee = round(sales_total * COUPANG_FEE_RATE)
        settlement_estimate = sales_total - market_fee
        settlement_source = "추정(수수료 12% 가정)"

    # 샵마인 값 매핑과 동일한 '배송비 포함' 금액. (엑셀 양식의 값으로 고를 수 있음)
    #   실결제금액(배송비포함)   = 상품 판매금액 + 배송비  (고객이 실제로 낸 금액)
    #   정산예정금액(배송비포함) = 정산예상금액 + 배송비
    _shipping_amt = order.get("shipping_fee") or 0
    paid_with_shipping = sales_total + _shipping_amt
    settlement_with_shipping = settlement_estimate + _shipping_amt

    # 택배사/송장번호는 우리가 직접 등록한 값을 우선 쓰고, 없으면(쿠팡 Wing에서
    # 직접 처리된 경우 등) 쿠팡이 알려주는 값을 대신 보여줍니다. 쿠팡은 아직
    # 송장이 없는 주문에 대해 "NONE"이라는 문자열을 그대로 돌려주기도 해서,
    # 그 값은 빈 값과 똑같이 취급합니다 (화면에 "NONE"이라고 뜨면 안 되므로).
    def _blank_if_none_sentinel(value):
        return None if not value or str(value).strip().upper() == "NONE" else value

    if order["delivery_company_code"]:
        courier_name = models.COURIER_CODES.get(order["delivery_company_code"], order["delivery_company_code"])
    else:
        courier_name = _blank_if_none_sentinel(order.get("market_delivery_company_name")) or "-"
    # 송장번호: 우리가 등록한 값 > 쿠팡Wing 값 > 배대지 가송장(quickstar_invoice) 순.
    #   ★가송장 폴백: 발송대기 주문은 아직 쿠팡에 송장을 등록하지 않아 invoice_number가 없지만,
    #   배대지 접수 시점에 CJ 가송장이 이미 quickstar_invoice에 저장돼 있습니다. 이걸 송장번호 칸에
    #   기본으로 보여주면 ① 자동입력을 안 눌러도 바로 발송처리할 수 있고 ② DB에 있는 값이라
    #   화면 새로고침(발송처리 실패 등)에도 안 사라집니다. (배송중 등 이미 등록된 주문은 invoice_number가
    #   먼저라 영향 없음)
    invoice_display = (
        _blank_if_none_sentinel(order["invoice_number"])
        or _blank_if_none_sentinel(order.get("market_invoice_number"))
        or _blank_if_none_sentinel(order.get("quickstar_invoice"))
        or "-"
    )

    remote_area = order.get("remote_area")
    remote_area_display = "-" if remote_area is None else ("예" if remote_area else "아니오")

    _full_row = {
        "No": row_no,
        "주문상태": order["work_status"],
        "퀵스타": "-",
        "쇼핑몰": MARKET_DISPLAY_NAME.get(order["market_name"], order["market_name"]),
        "쇼핑몰ID": "-",
        "별칭(쇼핑몰계정)": order.get("market_account_name") or "-",
        "주문일(약식)": _short_date(order["ordered_at"]),
        "구매자": order.get("orderer_name") or "-",
        "수령자": shipping.get("receiver_name") or "-",
        "주문고유코드": "-",
        "주문번호": order["market_order_id"],
        "주문순번": "-",
        "택배사": courier_name,
        "송장번호": invoice_display,
        "상품번호": format_item_ids(items),
        # 쿠팡상품번호: 쿠팡 상품 페이지 맨 아래에 보이는 "쿠팡상품번호"(productId - itemId).
        # 상세보기를 한 번 열면 조회·저장되어 여기에 채워집니다.
        "쿠팡상품번호": _coupang_product_codes(items),
        "상품명": format_items(items),
        "선택사항": format_options(items),
        "수량": format_quantity(items),
        "단가": "-",
        "추가구성": "-",
        # 수령자전화번호: 통관정보에 있는 실제 휴대폰번호(010...)
        # 수령자휴대전화: 쿠팡이 준 안심번호(0502...) - 실제 번호와 구분해서 같이 보여줍니다
        "수령자전화번호": privacy.format_phone(phone) if reveal else privacy.mask_phone(phone),
        "수령자휴대전화": (
            (privacy.format_phone(safe_phone) if reveal else privacy.mask_phone(safe_phone)) + " (안심번호)"
            if safe_phone
            else "-"
        ),
        "구매자전화": privacy.format_phone(orderer_phone) if reveal else privacy.mask_phone(orderer_phone),
        "구매자휴대전화": "-",
        "우편번호": shipping.get("zip_code") or "-",
        "주소": address,
        "배송메모": "-",
        # CS메모는 사용자가 상세내역에서 직접 적는 항목입니다.
        "CS메모": order.get("cs_memo") or "-",
        "배송구분": _format_item_field(items, "delivery_charge_type_name"),
        "총주문금액": f"{sales_total:,}",
        "옵션추가금액": "-",
        "마켓수수료금액": f"{market_fee:,}",
        "수수료율": "12%",
        "실수수료율": "12%",
        "공급금액": f"{settlement_estimate:,}",
        "배송비": f"{order['shipping_fee']:,}" if order["shipping_fee"] is not None else "-",
        "할인금액": "-",
        "정산예상금액": f"{settlement_estimate:,}",
        # 배송비 포함 금액(샵마인 값 매핑과 동일) — 엑셀 양식 값으로만 쓰이고 표에는 안 나옵니다.
        "실결제금액(배송비포함)": f"{paid_with_shipping:,}",
        "정산예정금액(배송비포함)": f"{settlement_with_shipping:,}",
        "주문일시": order["ordered_at"],
        "결제일시": order.get("paid_at") or "-",
        "클레임접수일시": "-",
        "발송예정일": _format_item_field(items, "estimated_shipping_date"),
        "인터파크 공급계약번호": "-",
        "배송번호": order["shipment_box_id"] or "-",
        "주문자ID": "-",
        "상품URL": " , ".join(_product_urls(items)) or "-",
        "판매자상품코드": _format_item_field(items, "seller_product_code"),
        "판매자옵션코드": _format_item_field(items, "seller_option_code"),
        "쇼핑몰계정고유번호": "-",
        "네이버 아이디 로그인": "-",
        "스토어명": "-",
        "개인통관고유부호": pccc if reveal else privacy.mask_pccc(pccc),
        "샵마인주문상태": "-",
        "송장입력": "완료" if invoice_display != "-" else "미입력",
        "보류여부": "-",
        "도서산간": remote_area_display,
        "정산추가정보": settlement_source,
        "발송일시": order["shipped_at"] or "-",
        "결제수단": "-",
        "배송방법": "-",
        "실결제금액": "-",
        "최초 엑셀생성 일시(로컬)": "-",
        "최근 엑셀생성 일시(로컬)": "-",
        "송장번호입력일시(로컬)": order["shipped_at"] or "-",
        "송장상품수량": "-",
        "추가구성수량": "-",
        "톡톡하기": "-",
        "주문일자(yyyyMMdd)": _yyyymmdd(order["ordered_at"]),
        "송장실결제금액": "-",
        "송장총주문금액": "-",
        "작업상태": order["work_status"],
        "메모(쇼핑몰계정)": "-",
        # 엑셀 양식에는 없지만, 통관검증 상태를 보여주기 위해 추가로 쓰는 항목입니다.
        "통관검증 상태": validation_status or "미검증",
        # 통과했어도, 수령자 대신 구매자 이름으로 재검증해서 통과한 경우처럼
        # 참고할 만한 메시지가 있으면 같이 보여줍니다 (메시지가 "("로 시작하면
        # 이런 참고 메모입니다).
        "불일치 사유": (
            shipping.get("validation_message") or "-"
            if validation_status not in (None, "미검증")
            and (
                validation_status != models.VALIDATION_STATUS_OFFICIAL_PASSED
                or (shipping.get("validation_message") or "").startswith("(")
            )
            else "-"
        ),
        "마지막 검증일시": shipping.get("last_validated_at") or "-",
        "발송 가능 여부": "가능" if validation_status in models.SHIPPABLE_VALIDATION_STATUSES else "불가",
        # 퀵스타 배대지 접수 결과
        "퀵스타 신청번호": order.get("quickstar_order_no") or "-",
        "퀵스타 운송장": order.get("quickstar_invoice") or "-",
        "퀵스타 접수일시": order.get("quickstar_submitted_at") or "-",
    }
    # 샵마인 '바꾸기설정'에서 정한 치환 규칙을 마지막에 적용합니다.
    # (규칙이 없으면 아무 것도 바꾸지 않아, 기존 동작 그대로입니다)
    return apply_replace_rules(_full_row)


# 바꾸기설정(치환 규칙)은 앱 전체 공통 설정 하나에 JSON으로 저장합니다.
_REPLACE_RULES_SETTING = "replace_rules"


def get_replace_rules() -> list:
    """저장된 치환 규칙 목록을 돌려줍니다. 각 규칙: {col, find, replace, mode}."""
    import json
    raw = settings_repository.get_setting(_REPLACE_RULES_SETTING)
    if not raw:
        return []
    try:
        rules = json.loads(raw)
        return rules if isinstance(rules, list) else []
    except (ValueError, TypeError):
        return []


def set_replace_rules(rules: list) -> None:
    """치환 규칙 목록을 저장합니다."""
    import json
    settings_repository.set_setting(_REPLACE_RULES_SETTING, json.dumps(rules, ensure_ascii=False))


def apply_replace_rules(row: dict) -> dict:
    """
    한 행(build_full_row 결과)에 저장된 치환 규칙을 적용합니다.
    - col == "(모든 컬럼)" 이면 모든 문자열 값에 적용, 아니면 그 컬럼에만.
    - mode == "전체" 이면 값이 find와 '완전히 같을 때만' replace로 통째 교체,
      "부분" 이면 값 안의 find를 replace로 바꿔 끼웁니다(부분 치환).
    규칙이 없으면 원본을 그대로 돌려줍니다.
    """
    rules = get_replace_rules()
    if not rules:
        return row
    for rule in rules:
        find = str(rule.get("find", ""))
        if not find:
            continue
        replace = str(rule.get("replace", ""))
        mode = rule.get("mode", "부분")
        target = rule.get("col", "(모든 컬럼)")
        cols = [c for c in row.keys() if c != "No"] if target == "(모든 컬럼)" else [target]
        for c in cols:
            if c not in row:
                continue
            val = row[c]
            if not isinstance(val, str):
                continue
            if mode == "전체":
                if val == find:
                    row[c] = replace
            else:
                if find in val:
                    row[c] = val.replace(find, replace)
    return row


def render_taobao_link_section(order: dict) -> None:
    """
    주문의 상품(판매자상품코드)별 타오바오 구매 링크를 보여주고 저장합니다.
    - 한 상품에 링크 여러 개(2~3개) 저장 가능 → 각각 '열기' 버튼(크롬으로 열림)
    - '링크 추가'로 계속 더할 수 있고, 저장하면 그 상품코드의 '모든 주문'에 적용됩니다.
    """
    from repositories import taobao_link_repository

    items = order.get("items") or []
    seen = set()
    any_product = False
    for item in items:
        code = str(item.get("seller_product_code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        any_product = True
        name = item.get("product_name") or ""
        links = taobao_link_repository.list_for_code(code)

        # 저장된 링크들 → 각각 '열기' 버튼 (여러 개 지원)
        for i, lk in enumerate(links):
            label = lk.get("label") or f"링크 {i + 1}"
            cols = st.columns([5, 1])
            with cols[0]:
                if st.button(f"🔗 타오바오 열기 · {label}", key=f"tb_open_{order['id']}_{lk['id']}", type="primary", width="stretch"):
                    if not open_in_chrome(lk["url"]):
                        st.warning("크롬을 찾지 못했습니다. 아래 주소를 복사해 여세요.")
                        st.code(lk["url"])
            with cols[1]:
                if st.button("삭제", key=f"tb_del_{order['id']}_{lk['id']}", width="stretch"):
                    taobao_link_repository.delete(lk["id"])
                    st.rerun()

        # 링크 추가 (여러 번 눌러 2~3개 계속 추가 가능)
        with st.expander(f"➕ 타오바오 링크 추가  (상품코드 {code}, 현재 {len(links)}개)", expanded=not links):
            new_label = st.text_input("구분 이름(선택)", key=f"tb_lbl_{order['id']}_{code}",
                                      placeholder="예: 1번 / 저렴이 / 예비")
            new_url = st.text_input(
                "타오바오 상품 URL", key=f"tb_url_{order['id']}_{code}",
                placeholder="https://item.taobao.com/... 또는 https://detail.tmall.com/...",
                help="여기 저장하면 이 상품(코드)의 모든 주문에서 위 버튼으로 열립니다. 여러 개 추가 가능.",
            )
            if st.button("💾 링크 추가 저장", key=f"tb_add_{order['id']}_{code}"):
                if new_url.strip():
                    taobao_link_repository.add(code, name, new_url, label=new_label)
                    st.success("추가했습니다. 이 상품의 모든 주문에 적용됩니다.")
                    st.rerun()
                else:
                    st.warning("URL을 입력해주세요.")

    if not any_product:
        st.caption("이 주문에는 판매자상품코드가 없어 타오바오 링크를 연결할 수 없습니다.")


def render_full_detail(order: dict, reveal: bool, with_cs_memo: bool = True) -> None:
    """
    with_cs_memo=False면 CS메모 편집기를 그리지 않습니다(클레임 상세처럼 상위에서
    CS메모를 따로 그릴 때, 같은 주문에 편집기가 두 번 그려져 폼 키가 충돌하는 것 방지).

    평상시(행을 선택하지 않았을 때)에는 화면에 나타나지 않고, 표에서 행을
    하나 클릭했을 때만 나타나는 상세정보입니다. 엑셀 양식의 72개 항목을
    전부 항목/값 형태로 보여주고, 통관검증에서 문제가 있었던 항목(이름/
    전화번호/개인통관고유부호/우편번호)에는 ⚠ 표시와 함께 구체적인 사유를
    붙여줍니다.
    """
    # 상세내역에 '쿠팡상품번호'와 '상품URL'을 정확히 채우려면 productId가 필요합니다.
    # build_full_row로 표를 만들기 "전에" 그 상품만 조회해서(캐시에 없을 때만)
    # 저장해 둡니다. 그래야 아래 표에 처음부터 올바른 값이 나옵니다.
    try:
        product_link_service.ensure_links_for_order(order)
    except Exception:
        # 링크 조회가 실패해도 상세보기 자체는 정상적으로 보여야 합니다.
        pass

    row = build_full_row(order, order["id"], reveal=reveal)
    shipping = order["shipping"] or {}

    def _with_check(value: str, check_message: str) -> str:
        return f"{value} ⚠ {check_message}" if check_message else value

    field_check_messages = {
        "수령자": shipping.get("name_check_message"),
        "수령자전화번호": shipping.get("phone_check_message"),
        "개인통관고유부호": shipping.get("pccc_check_message"),
        "우편번호": shipping.get("zip_check_message"),
    }

    st.subheader("상세내역")

    items = order.get("items") or []
    link_infos = [_item_link_info(item) for item in items if item.get("market_item_id")]

    # 쿠팡 상품 페이지를 "크롬으로" 여는 버튼을 눈에 띄게 위쪽에 둡니다.
    # (인앱 브라우저가 아니라 사용자 컴퓨터의 크롬 창으로 열립니다)
    for index, info in enumerate(link_infos):
        label = "🔗 크롬으로 쿠팡 상품 페이지 열기"
        if len(link_infos) > 1:
            label += f" (상품 {index + 1})"
        if st.button(label, key=f"open_chrome_{order['id']}_{index}", type="secondary"):
            if not open_in_chrome(info["url"]):
                # 크롬이 없으면 주소를 보여줘서 직접 열 수 있게 합니다.
                st.warning("크롬을 찾지 못했습니다. 아래 주소를 복사해 여세요.")
                st.code(info["url"])
        if info["코드표시"]:
            st.caption(f"쿠팡상품번호: {info['코드표시']}")

    # 상품별 타오바오 구매 링크(판매자상품코드 기준). 한 번 저장하면 그 상품의 모든 주문에서 바로 이동.
    render_taobao_link_section(order)

    if with_cs_memo:
        render_cs_memo_editor(order)

    detail_rows = [
        (col, _with_check(str(row[col]), field_check_messages.get(col)))
        for col in FULL_COLUMNS
        if col != "No"
    ]
    detail_rows += [(col, row[col]) for col in VALIDATION_COLUMNS + QUICKSTAR_COLUMNS]

    detail_df = pd.DataFrame(detail_rows, columns=["항목", "값"]).set_index("항목")
    st.table(detail_df)


def render_cs_memo_editor(order: dict) -> None:
    """
    CS메모(고객 특이사항)를 직접 적고 저장하는 칸입니다. 주문마다 따로 저장되고,
    저장하면 목록 표의 "CS메모" 칸에도 바로 반영됩니다.
    """
    saved_memo = order.get("cs_memo") or ""
    with st.form(f"cs_memo_form_{order['id']}"):
        memo = st.text_area(
            "CS메모 (고객 특이사항)",
            value=saved_memo,
            height=100,
            placeholder="예: 부재 시 경비실 맡겨달라고 함 / 통관번호 재확인 필요",
        )
        if st.form_submit_button("메모 저장"):
            order_repository.update_cs_memo(order["id"], memo.strip())
            st.session_state["cs_memo_saved"] = order["market_order_id"]
            st.rerun()

    if st.session_state.get("cs_memo_saved") == order["market_order_id"]:
        st.session_state.pop("cs_memo_saved")
        st.success("CS메모를 저장했습니다.")


def courier_label(code: str) -> str:
    """택배사 코드를 "이름 (코드)" 형태로 바꿉니다. (이름이 겹치는 택배사가 있어서 코드도 같이 붙입니다)"""
    return f"{models.COURIER_CODES.get(code, code)} ({code})"


COURIER_LABEL_TO_CODE = {courier_label(code): code for code in models.COURIER_CODES}
# 택배사를 아직 안 고른 칸에 넣어둘 값입니다. (None을 쓰면 드롭다운에 "None"이라는
# 글자가 그대로 보이면서 선택이 안 되기 때문에, 실제 선택지 하나로 만들어 둡니다)
COURIER_UNSET_LABEL = "(선택 안 함)"


def grid_has_selection(key: str) -> bool:
    """
    그리드를 '렌더하기 전에' 지금 선택된 행이 있는지 알아냅니다. st_aggrid는 위젯 값을
    st.session_state[위젯키]에 저장하고, 선택이 바뀌면 새로고침 '직전에' 그 값이 갱신되므로,
    여기서 미리 읽으면 폭 비율(전체폭↔3:2)을 한 번에 정할 수 있습니다(강제 새로고침 불필요).
    """
    sel, _ = grid_selection_info(key)
    return sel > 0


def grid_selection_info(key: str):
    """그리드 렌더 전에 (선택된 행 수, 전체 행 수)를 미리 읽습니다. '전체선택'인지 판단용."""
    wk = st.session_state.get(f"aggrid_widgetkey:{key}")
    if not wk:
        return 0, 0
    cv = st.session_state.get(wk)
    if not isinstance(cv, dict):
        return 0, 0
    try:
        nodes = cv.get("nodes") or []
        sel = sum(1 for n in nodes if n.get("isSelected"))
        return sel, len(nodes)
    except Exception:
        return 0, 0


def _clear_grid_selection(key: str) -> None:
    """'상세 닫기'용: 표의 체크를 모두 비웁니다. nonce를 올려 그리드를 새로 그려(선택 초기화)
    상세를 닫고, 상세 대상 고정도 해제합니다. (표는 다시 전체폭으로 돌아갑니다)
    ※ 입력한 택배사·송장번호(edited_records)는 '지우지 않습니다' — 상세 닫아도 유지되어야 하니까요."""
    st.session_state[f"aggrid_reset_nonce:{key}"] = st.session_state.get(f"aggrid_reset_nonce:{key}", 0) + 1
    st.session_state.pop(f"{key}_prev_sel_ids", None)
    st.session_state.pop(f"{key}_detail_target_id", None)
    st.session_state[f"{key}_selected_orders"] = []


def pick_detail_target(selected: list, key: str):
    """
    체크된 주문(records) 목록에서 '상세로 보여줄 하나'를 고릅니다. 여러 개를 체크했을 때
    '방금 새로 체크한' 것을 우선 보여줘서, 두 번째로 체크한 행의 상세가 바로 뜨게 합니다.
    (직전 선택을 세션에 기억해 비교합니다. id가 없거나 중복이면 그냥 마지막 것을 씁니다.)
    """
    prev_key = f"{key}_prev_sel_ids"
    target_key = f"{key}_detail_target_id"
    if not selected:
        st.session_state.pop(prev_key, None)
        st.session_state.pop(target_key, None)
        return None
    ids = [o.get("id") if isinstance(o, dict) else None for o in selected]
    if any(i is None for i in ids) or len(set(ids)) != len(ids):
        return selected[-1]
    prev = st.session_state.get(prev_key, [])
    newly = [i for i in ids if i not in prev]
    st.session_state[prev_key] = ids

    # ★보던 상세 주문을 '고정'합니다. 상세 안에서 버튼/입력(타오바오 링크 등)을 눌러
    #   화면이 새로고침돼도 같은 주문이 유지되고, '새로 체크한 주문'이 있을 때만 그쪽으로
    #   전환합니다. (예전엔 선택목록의 마지막을 다시 골라서, 상호작용 때 다른 주문이 열렸음)
    cur = st.session_state.get(target_key)
    if newly:
        cur = newly[-1]            # 새로 체크한 주문으로 상세 전환
    elif cur not in ids:
        cur = ids[-1]              # 보던 주문이 선택 해제됐으면 마지막 선택으로
    st.session_state[target_key] = cur
    for order in selected:
        if order.get("id") == cur:
            return order
    return selected[-1]


@st.fragment
def _render_table_with_fragment(key, summary_columns, sorted_rows, sorted_orders, table_width_percent, detail_renderer, multi_select=False):
    """
    발송대기와 동일한 AG-Grid 표(왼쪽 체크박스 + 머리글 전체선택, 마우스로 컬럼 드래그
    순서변경·자동저장, 행 순번 드래그)를 그립니다. 아무것도 체크 안 하면 표가 전체폭으로
    꽉 차고, 체크하면 표(왼쪽)+상세(오른쪽)로 나뉩니다. 신규주문·배송중·배송완료·구매확정 공용.

    ★ st.fragment: 체크(선택)할 때 페이지 전체가 아니라 이 부분만 다시 그려집니다.
      그래서 스크롤이 위로 튀지 않고, 상세만 제자리에서 바뀝니다.
    선택 결과는 세션(f"{key}_selected_orders")에 저장 → render_full_table가 그 값을 돌려줍니다.
    """
    from ui import aggrid_table

    st.caption(
        "표 왼쪽 체크박스를 체크하면 그 주문 상세가 오른쪽 옆에 나타납니다. 맨 위 머리글 체크박스로 "
        "전체선택할 수 있고, 체크한 주문들로 일괄작업을 합니다. **컬럼(항목)은 마우스로 끌어 "
        "순서를 바꿀 수 있고, 그 순서는 자동 저장됩니다.**"
    )

    # 선택(체크박스)은 'No' 컬럼에 달리므로, 사용자가 항목에서 No를 빼도 표에는 넣어줍니다.
    cols = list(summary_columns)
    if sorted_rows and "No" in sorted_rows[0] and "No" not in cols:
        cols = ["No"] + cols
    grid_df = pd.DataFrame(sorted_rows)[cols] if sorted_rows else pd.DataFrame(columns=cols)

    # ★컬럼 '구조'는 항상 2단으로 고정하고, 폭 '비율'만 바꿉니다. 구조(컨테이너↔컬럼)를
    #   바꾸면 AG-Grid가 재생성되어 체크가 풀리고 화면이 튀기 때문입니다. 상세가 없을 땐
    #   오른쪽 칸을 거의 0으로 줄여 표가 꽉 차 보이고, 체크하면 3:2로 나뉩니다.
    # 선택 유무를 그리드 '렌더 전에' 세션(위젯값)에서 미리 읽어, 강제 새로고침 없이 한 번에
    #   폭 비율을 맞춥니다. (강제 새로고침이 스크롤을 위로 튀게 하던 원인이라 제거)
    # 선택된 수 / 전체 수를 미리 읽어, '전체선택'이면 상세를 안 띄우고 표를 전체폭으로 둡니다.
    _sel_n, _tot_n = grid_selection_info(key)
    # '상세 닫기'는 그리드를 다시 그리지(remount) 않고 상세 패널만 숨깁니다 → 체크·정렬 유지.
    #   선택 개수가 바뀌면(새로 체크/해제) 숨김을 풀어 상세가 다시 보이게 합니다.
    _hidden_key = f"{key}_detail_hidden"
    _lastcount_key = f"{key}_detail_lastcount"
    if st.session_state.get(_lastcount_key) != _sel_n:
        st.session_state[_hidden_key] = False
        st.session_state[_lastcount_key] = _sel_n
    # ★전체선택(상세 숨김)은 '2건 이상 전부 체크'일 때만. 검색 결과가 1건이라 그 1건을 체크한 경우는
    #   전체선택이 아니라 '단일 선택'이므로 상세를 보여줘야 함(1>=1을 전체선택으로 오인하던 버그 수정).
    all_selected = _tot_n > 1 and _sel_n >= _tot_n
    show_detail = _sel_n > 0 and not all_selected and not st.session_state.get(_hidden_key)
    # ★상세 패널을 표와 '같은 고정 높이(스크롤 박스)'로 만듭니다. 그러면 체크/해제해도
    #   페이지 전체 높이가 안 변해서, ① 요약바가 저 아래로 안 밀리고 ② 해제 시 스크롤이
    #   위로 튀지 않습니다. (grid_h는 aggrid_table.render_orders_grid의 높이 계산과 동일)
    grid_h = max(300, min(44 + max(len(sorted_orders), 1) * 34 + 6, 760))
    # 표:상세 폭 비율은 위(render_full_table)의 '표/상세 너비' 슬라이더 값을 그대로 씁니다.
    # (예전엔 여기 슬라이더가 하나 더 있어 두 개가 겹쳐 헷갈렸음 → 하나로 통일)
    table_pct = max(30, min(int(table_width_percent), 90))
    # 상세를 안 보일 땐(선택 안 함 · 전체선택) 표가 전체폭, 일부 체크했을 때만 표:상세로 나뉩니다.
    col_table, col_detail = st.columns([table_pct, 100 - table_pct] if show_detail else [100, 1])
    with col_table:
        selected, _edited_df, _clicked = aggrid_table.render_orders_grid(
            grid_df, key=key, orders=sorted_orders, height=grid_h
        )
        st.caption(f"총 {len(sorted_rows)}건")

    st.session_state[f"{key}_selected_orders"] = selected
    target = pick_detail_target(selected, key) if show_detail else None

    with col_detail:
        if target is not None:
            with st.container(height=max(grid_h, 760)):
                if st.button("✕ 상세 닫기", key=f"{key}_close_detail", width="stretch"):
                    # 그리드를 remount하지 않고 상세만 숨김 → 체크·정렬(차순) 유지됨
                    st.session_state[f"{key}_detail_hidden"] = True
                    # 보통은 fragment만 다시 그리면 되지만(체크·정렬 유지), 전체 리런 도중
                    # 눌리면 scope="fragment"가 예외를 내므로 그때는 전체 리런으로 넘어갑니다.
                    try:
                        st.rerun(scope="fragment")
                    except st.errors.StreamlitAPIException:
                        st.rerun()
                detail_renderer(target)

    return None, selected, selected, None


def _restore_persisted_edits(grid_df, key: str, editable_cols: list) -> None:
    """표 안에서 사용자가 입력한 편집값(택배사·송장번호 등)을, 렌더 '직전에' grid_df에
    다시 덮어써서 유지합니다.

    ★왜 필요한가: render_grid_detail은 @st.fragment 입니다. '상세 닫기'·표 안 편집처럼
      fragment만 다시 그려질 때는 부모 화면(예: 발송대기)의 코드가 다시 돌지 않아,
      부모가 하던 '입력값 복원'이 안 됩니다. 게다가 '상세 닫기'는 표를 새 key로
      리마운트해서, 방금 입력한 값이 그리드에서 사라집니다. 그래서 여기(=fragment 안)에서
      직전에 저장해둔 편집값을 반드시 다시 얹어야 송장/택배사가 유지됩니다.
    매칭: '주문번호'가 있으면 그것으로(정렬이 바뀌어도 안전), 없으면 _idx(행 위치)로."""
    from ui import aggrid_table

    prev = st.session_state.get(f"{key}_edited_records", []) or []
    if not prev:
        return
    cols = [c for c in (editable_cols or []) if c in grid_df.columns]
    if not cols:
        return

    def _apply(row_idx, col, val):
        if val is None:
            return
        s = str(val).strip()
        # 빈칸·nan·"-"(송장 없음 기본표시)는 '입력 안 함'이므로 덮어쓰지 않습니다.
        if s == "" or s.lower() == "nan" or s == "-":
            return
        grid_df.at[row_idx, col] = val

    id_col = "주문번호" if "주문번호" in grid_df.columns else None
    if id_col:
        # 주문번호 → 마지막 편집레코드 (정렬이 바뀌어도 올바른 주문에 붙습니다)
        edit_by_id = {}
        for rec in prev:
            oid = str(rec.get(id_col) or "").strip()
            if oid:
                edit_by_id[oid] = rec
        for i in grid_df.index:
            rec = edit_by_id.get(str(grid_df.at[i, id_col]).strip())
            if not rec:
                continue
            for col in cols:
                _apply(i, col, rec.get(col))
    else:
        for rec in prev:
            try:
                idx = int(rec.get(aggrid_table.IDX_COL))
            except (TypeError, ValueError):
                continue
            if idx not in grid_df.index:
                continue
            for col in cols:
                _apply(idx, col, rec.get(col))


@st.fragment
def render_grid_detail(grid_df, key, orders, detail_renderer, empty_caption="표 왼쪽 체크박스를 체크하면 여기에 상세가 나타납니다.",
                       ratios=(3, 2), height=760, editable=None, button_col=None, on_result=None):
    """
    표(왼쪽)+상세(오른쪽)를 fragment로 그리는 공용 헬퍼입니다. 발송대기·교환·취소·반품·CS메모가 씁니다.
      - 아무것도 체크 안 하면 표가 전체폭, 체크하면 표+상세로 나뉨
      - 체크(선택)는 fragment 안에서 처리 → 페이지 스크롤이 위로 안 튐
      - '방금 새로 체크한' 행의 상세를 보여줌(pick_detail_target)
    결과는 세션에 저장돼, 바깥에서 아래 키로 읽습니다(버튼 등 일괄작업용):
      st.session_state[f"{key}_selected_orders"], [f"{key}_edited_records"]
    on_result(selected, edited_df, clicked_raw): 표 바로 뒤(같은 fragment 안)에서 처리할 콜백
      (예: 표 안 '퀵스타 연동' 버튼 클릭 → 다이얼로그). 없으면 생략.
    detail_renderer(target_row): 오른쪽 상세를 그립니다.
    """
    from ui import aggrid_table

    # ★fragment만 다시 그려져도(상세 닫기·표 안 편집) 입력한 택배사·송장번호가 유지되도록,
    #   그리드를 그리기 '직전에' 직전 편집값을 grid_df에 복원합니다. (_idx 위치로 매칭)
    grid_df = grid_df.reset_index(drop=True)
    if editable:
        _restore_persisted_edits(grid_df, key, list(editable.keys()))

    # ★컬럼 '구조'는 항상 2단으로 고정하고, 폭 '비율'만 바꿉니다. 구조(컨테이너↔컬럼)를
    #   바꾸면 AG-Grid가 재생성되어 체크가 풀리고 화면이 튀기 때문입니다. 상세 없을 땐
    #   오른쪽 칸을 거의 0으로 줄여 표가 꽉 차 보이고, 체크하면 지정한 비율로 나뉩니다.
    # 선택 유무를 그리드 '렌더 전에' 세션(위젯값)에서 미리 읽어, 강제 새로고침 없이 한 번에
    #   폭 비율을 맞춥니다. (강제 새로고침이 스크롤을 위로 튀게 하던 원인이라 제거)
    # 선택된 수/전체 수를 미리 읽어, '전체선택'이면 상세를 안 띄우고 표를 전체폭으로 둡니다.
    _sel_n, _tot_n = grid_selection_info(key)
    # '상세 닫기'는 그리드를 다시 그리지(remount) 않고 상세 패널만 숨깁니다 → 체크·정렬 유지.
    #   선택 개수가 바뀌면(새로 체크/해제) 숨김을 풀어 상세가 다시 보이게 합니다.
    _hidden_key = f"{key}_detail_hidden"
    _lastcount_key = f"{key}_detail_lastcount"
    if st.session_state.get(_lastcount_key) != _sel_n:
        st.session_state[_hidden_key] = False
        st.session_state[_lastcount_key] = _sel_n
    # ★전체선택(상세 숨김)은 '2건 이상 전부 체크'일 때만. 검색 결과가 1건이라 그 1건을 체크한 경우는
    #   전체선택이 아니라 '단일 선택'이므로 상세를 보여줘야 함(1>=1을 전체선택으로 오인하던 버그 수정).
    all_selected = _tot_n > 1 and _sel_n >= _tot_n
    show_detail = _sel_n > 0 and not all_selected and not st.session_state.get(_hidden_key)
    # 상세 패널을 표와 같은 고정 높이(스크롤 박스)로 → 체크/해제해도 페이지 높이가 안 변해
    # 스크롤이 위로 안 튀고 아래 요약도 안 밀립니다.
    grid_h = max(300, min(44 + max(len(orders), 1) * 34 + 6, min(height, 760)))
    # 표 너비 조절 슬라이더 — 상세를 열었을 때 표가 차지하는 비율(%). 저장되어 유지됩니다.
    _w_key = f"table_width_pct:{key}"
    _saved_w = int(settings_repository.get_setting(_w_key, "60") or "60")
    _wcol, _ = st.columns([2, 5])
    with _wcol:
        detail_pct = st.slider("상세내역 너비(%)", 10, 70, min(max(100 - _saved_w, 10), 70), 5, key=f"{key}_width_slider",
                               help="오른쪽 상세내역이 차지하는 가로 비율입니다. 높일수록 상세가 넓어집니다. (예: 60 → 상세가 60%)")
    table_pct = 100 - detail_pct
    if table_pct != _saved_w:
        settings_repository.set_setting(_w_key, str(table_pct))
    # 상세를 안 보일 땐(선택 안 함 · 전체선택) 표가 전체폭, 일부 체크했을 때만 표:상세로 나뉩니다.
    c_table, c_detail = st.columns([table_pct, 100 - table_pct] if show_detail else [100, 1])

    with c_table:
        selected, edited_df, clicked = aggrid_table.render_orders_grid(
            grid_df, key=key, orders=orders, editable=editable, height=grid_h, button_col=button_col
        )

    st.session_state[f"{key}_selected_orders"] = selected
    # ★입력값(택배사·송장번호)이 사라지지 않게: 그리드가 값을 정상적으로 돌려줬을 때만
    #   저장하고, 비어있으면(리마운트 직후 등) 기존 값을 그대로 둡니다. (덮어써서 지우지 않음)
    try:
        _recs = edited_df.to_dict("records")
        if _recs:
            st.session_state[f"{key}_edited_records"] = _recs
    except Exception:
        pass

    if on_result is not None:
        on_result(selected, edited_df, clicked)

    target = pick_detail_target(selected, key) if show_detail else None

    with c_detail:
        if target is not None:
            with st.container(height=max(grid_h, 760)):
                if st.button("✕ 상세 닫기", key=f"{key}_close_detail", width="stretch"):
                    # 그리드를 remount하지 않고 상세만 숨김 → 체크·정렬(차순) 유지됨
                    st.session_state[f"{key}_detail_hidden"] = True
                    # 보통은 fragment만 다시 그리면 되지만(체크·정렬 유지), 전체 리런 도중
                    # 눌리면 scope="fragment"가 예외를 내므로 그때는 전체 리런으로 넘어갑니다.
                    try:
                        st.rerun(scope="fragment")
                    except st.errors.StreamlitAPIException:
                        st.rerun()
                detail_renderer(target)


def render_full_table(filtered_rows: list, filtered_orders: list, key: str, invoice_editable: bool = False, detail_renderer=None, multi_select=False):
    """
    엑셀 양식 전체 컬럼 표를 그리고, 정렬 기준/너비/높이 조절과 행 선택 기능을
    함께 제공합니다. 여러 목록 화면(신규주문/발송대기/배송중/배송완료/구매확정)이
    전부 이 함수로 표를 그립니다.

    마우스로 표 헤더를 드래그해서 정렬/컬럼너비를 바꾸는 건 Streamlit 표
    자체의 기능이라 저장이 안 됩니다 (새로고침하면 초기화). 그래서 정렬은
    별도의 드롭다운으로 만들었고, 여기서 고른 정렬 기준은 DB(app_settings)에
    저장되어 프로그램을 완전히 껐다 켜도 유지됩니다.

    invoice_editable=True(발송대기 화면)이면 표 안에서 택배사/송장번호를 직접
    입력할 수 있는 편집 가능한 표로 그립니다. 이때는 Streamlit 표의 행 클릭
    선택 기능을 쓸 수 없어서, 맨 앞에 "선택" 체크박스 칸(일괄작업용)과
    "상세" 버튼 칸(상세정보 보기용)을 대신 넣습니다.

    돌려주는 값: (col_detail, 체크된 주문 목록, 상세보기할 주문 목록, 편집결과)
      - 편집 불가 표에서는 행 클릭이 곧 선택이자 상세보기라, 두 목록이 같습니다.
      - 편집결과는 invoice_editable=True일 때만 채워지고, 그 외에는 None입니다.
        [{"order": 주문, "택배사코드": str|None, "송장번호": str}, ...] 형태입니다.
    """
    all_columns = ["No"] + VALIDATION_COLUMNS + QUICKSTAR_COLUMNS + [col for col in FULL_COLUMNS if col != "No"]

    # ---- 표시할 항목과 그 순서 (직접 정하면 저장되어 재시작해도 유지됩니다) ----
    saved_order_text = settings_repository.get_setting(f"column_order:{key}")
    if saved_order_text:
        saved_columns = [col for col in saved_order_text.split("|") if col in all_columns]
    else:
        saved_columns = all_columns

    with st.expander("표시할 항목·순서 바꾸기", expanded=False):
        st.caption(
            "보고 싶은 항목만 골라서, 고른 순서대로 표에 나옵니다. "
            "여기서 정한 순서는 저장되어 프로그램을 다시 켜도 그대로 유지됩니다."
        )
        summary_columns = st.multiselect(
            "표시할 항목 (고른 순서대로 표시)",
            options=all_columns,
            default=saved_columns,
            key=f"{key}_column_order",
        )
        if st.button("기본 순서로 되돌리기", key=f"{key}_reset_columns"):
            settings_repository.set_setting(f"column_order:{key}", "|".join(all_columns))
            st.session_state.pop(f"{key}_column_order", None)
            st.rerun()

    if not summary_columns:
        summary_columns = all_columns
    if summary_columns != saved_columns:
        settings_repository.set_setting(f"column_order:{key}", "|".join(summary_columns))

    # 편집 가능한 표에서는 택배사/송장번호 칸이 반드시 있어야 입력을 할 수 있습니다.
    if invoice_editable:
        for required_col in ("택배사", "송장번호"):
            if required_col not in summary_columns:
                summary_columns = summary_columns + [required_col]

    # 기본 정렬을 '주문일(약식) 내림차순'(최신 주문 위로)로 둡니다. 이 드롭다운으로
    # 정한 정렬은 저장되어, 상세내역을 눌러 화면이 새로고침돼도 풀리지 않습니다.
    # (표 머리글 클릭 정렬은 Streamlit 기본 기능이라 새로고침 시 풀립니다)
    default_sort_col = "주문일(약식)" if "주문일(약식)" in summary_columns else "No"
    saved_sort_col = settings_repository.get_setting(f"sort_col:{key}", default_sort_col)
    saved_sort_dir = settings_repository.get_setting(f"sort_dir:{key}", "내림차순")
    if saved_sort_col not in summary_columns:
        saved_sort_col = summary_columns[0]
    # 표 너비도 정렬처럼 저장합니다. 최대(95%)로 기본값을 잡아서, 따로 안
    # 건드려도 표가 옆으로 꽉 차게 나오게 했습니다. (100%로 하면 오른쪽
    # 상세정보 칸이 완전히 사라져버려서, 최소한의 자리는 남겨뒀습니다)
    saved_width_percent = min(int(settings_repository.get_setting(f"table_width:{key}", "95")), 95)

    col_sort_col, col_sort_dir, col_width = st.columns([2, 1, 2])
    with col_sort_col:
        sort_col = st.selectbox(
            "정렬 기준",
            options=summary_columns,
            index=summary_columns.index(saved_sort_col),
            key=f"{key}_sort_col",
        )
    with col_sort_dir:
        sort_dir = st.selectbox(
            "정렬 방향",
            options=["오름차순", "내림차순"],
            index=0 if saved_sort_dir == "오름차순" else 1,
            key=f"{key}_sort_dir",
        )
    with col_width:
        st.write("")
        _detail_width = st.slider(
            "상세내역 너비(%)", min_value=10, max_value=70, value=min(max(100 - saved_width_percent, 10), 70), step=5,
            key=f"{key}_width",
            help="오른쪽 상세내역이 차지하는 가로 비율입니다. 높일수록 상세가 넓어집니다. (예: 60 → 상세가 60%)",
        )
        table_width_percent = 100 - _detail_width

    # ★정렬은 항상 저장: 표(aggrid)가 sort_col:{key}/sort_dir:{key}를 읽어 머리글 정렬
    #   화살표를 그리므로, 기본값이어도 DB에 있어야 화살표가 뜨고 새로고침 후 안 풀립니다.
    if settings_repository.get_setting(f"sort_col:{key}") != sort_col:
        settings_repository.set_setting(f"sort_col:{key}", sort_col)
    if settings_repository.get_setting(f"sort_dir:{key}") != sort_dir:
        settings_repository.set_setting(f"sort_dir:{key}", sort_dir)
    if table_width_percent != saved_width_percent:
        settings_repository.set_setting(f"table_width:{key}", str(table_width_percent))

    paired = list(zip(filtered_rows, filtered_orders))
    paired.sort(key=lambda pair: pair[0].get(sort_col), reverse=(sort_dir == "내림차순"))
    sorted_rows = [pair[0] for pair in paired]
    sorted_orders = [pair[1] for pair in paired]

    # 상세 렌더 함수가 주어지면, 표+상세를 fragment(부분 새로고침)로 그립니다.
    # (체크할 때 화면 전체가 아니라 그 부분만 갱신 → 스크롤이 위로 튀지 않음)
    # fragment는 반환값을 바깥으로 못 넘기므로, 선택 결과는 세션에서 읽어 돌려줍니다.
    if detail_renderer is not None and not invoice_editable:
        _render_table_with_fragment(
            key, summary_columns, sorted_rows, sorted_orders, table_width_percent, detail_renderer, multi_select
        )
        selected = st.session_state.get(f"{key}_selected_orders", [])
        return None, selected, selected, None

    # ---- 상세정보를 지금 보여줄 상태인지 먼저 판단합니다 ----
    # 상세정보가 없을 때는 표가 화면 전체를 쓰고, 상세정보를 열었을 때만
    # 오른쪽 칸을 만들어 화면을 나눕니다 (샵마인과 같은 방식).
    detail_row = None
    if invoice_editable:
        detail_row = st.session_state.get(f"{key}_detail_row")
        has_detail = detail_row is not None and 0 <= detail_row < len(sorted_orders)
    else:
        # 편집 불가 표는 행 클릭(선택) 자체가 상세보기라, 위젯에 저장된 선택 상태를 봅니다.
        # 표를 그리기 전이라 아직 event를 못 받아서, 세션에 남아있는 선택 상태를 봅니다.
        table_state = st.session_state.get(f"{key}_table")
        if table_state is None:
            has_detail = False  # 아직 아무 행도 선택하지 않음
        else:
            try:
                selected_rows = table_state["selection"]["rows"]
                has_detail = len(selected_rows) == 1
                if has_detail:
                    detail_row = selected_rows[0]
            except Exception:
                # 선택 상태를 읽는 방법이 바뀌었더라도 상세정보가 안 보이는 일은
                # 없도록, 못 읽으면 예전처럼 화면을 나눠둡니다 (안전한 쪽으로).
                has_detail = True
        if detail_row is not None and not (0 <= detail_row < len(sorted_orders)):
            detail_row = None

    if has_detail:
        # 상세내역이 열렸을 때는 오른쪽 상세 칸이 최소 30%는 되도록 보장합니다.
        # (표 너비 기본값이 95%라 그대로 두면 상세 칸이 5%뿐이라 글자가 짤려서 보입니다)
        _detail_percent = max(30, 100 - table_width_percent)
        col_table, col_detail = st.columns([100 - _detail_percent, _detail_percent])
    else:
        col_table = st.container()
        col_detail = st.container()

    if has_detail:
        with col_detail:
            if st.button("✕ 닫기", key=f"{key}_close_detail"):
                st.session_state.pop(f"{key}_detail_row", None)
                st.session_state.pop(f"{key}_table", None)
                st.rerun()

    with col_table:
        if invoice_editable:
            st.caption(
                "표 안에서 **택배사**와 **송장번호**를 직접 입력할 수 있습니다. 입력한 뒤 아래 "
                "'🚚 발송하기' 버튼을 누르면 배송중으로 넘어갑니다. 상세정보를 보려면 '상세보기'를 누르세요."
            )
        else:
            st.caption(
                "표를 옆으로 넘기면 나머지 항목이 보입니다. 행을 클릭하면 오른쪽에 상세정보가 나타납니다. "
                "표 안에서 아래로 스크롤해도 맨 위 항목 이름 줄은 고정되어 계속 보입니다."
            )
        summary_df = pd.DataFrame(sorted_rows)[summary_columns]
        # 표 높이는 실제 행 개수에 맞춰서 잡습니다. 주문이 적으면 그만큼만 차지해서
        # 아래에 빈 줄이 남지 않고, 많으면 600까지만 커지고 그 안에서 스크롤됩니다.
        # (높이를 행 개수만큼 무한정 늘리면 표 자체 스크롤이 없어져서 "맨 위 항목
        #  이름 줄 고정"이 소용없어지기 때문에, 최대치를 둡니다)
        fitted_height = TABLE_ROW_HEIGHT * (len(sorted_rows) + 1) + 3  # +1은 항목 이름 줄
        default_height = max(100, min(fitted_height, 600))
        table_height = st.slider(
            "표 높이 조절", min_value=100, max_value=3000, value=default_height, step=50, key=f"{key}_height"
        )

        if invoice_editable:
            # 편집 가능한 표에는 Streamlit이 제공하는 "머리글 전체선택 체크박스"가
            # 없어서(편집 표의 한계 - 회색 헤더 칸 안에는 넣을 수 없습니다), 표 바로
            # 위 왼쪽(선택 칸 위)에 진짜 체크박스를 하나 둡니다. 체크하면 아래 '선택'
            # 칸의 모든 행이 한 번에 선택되고, 해제하면 모두 풀립니다.
            all_selected = bool(st.session_state.get(f"{key}_select_all", False))
            col_toggle, _spacer = st.columns([1, 6])
            with col_toggle:
                new_selected = st.checkbox(
                    "전체 선택",
                    value=all_selected,
                    key=f"{key}_select_all_cb",
                    help="아래 '선택' 칸의 모든 행을 한 번에 선택/해제합니다.",
                )
            if new_selected != all_selected:
                st.session_state[f"{key}_select_all"] = new_selected
                # 표에 남아있던 개별 체크 기록을 지워야 전체선택/해제가 그대로 반영됩니다.
                st.session_state.pop(f"{key}_editor", None)
                st.rerun()

        if not invoice_editable:
            event = st.dataframe(
                summary_df,
                hide_index=True,
                on_select="rerun",
                selection_mode="multi-row",
                key=f"{key}_table",
                height=table_height,
                row_height=TABLE_ROW_HEIGHT,
                width="stretch",
            )
            st.caption(f"총 {len(sorted_rows)}건")
            selected_orders = [sorted_orders[position] for position in event.selection.rows]
            # 편집 불가 표에서는 행을 클릭하는 것이 곧 "선택"이자 "상세보기"입니다.
            return col_detail, selected_orders, selected_orders, None

        # ---- 편집 가능한 표 (발송대기 화면) ----
        # 편집 가능한 표(st.data_editor)는 행 클릭 선택 기능이 없어서, 맨 앞에
        # "선택" 체크박스 칸과 "상세" 버튼 칸을 직접 만들어 넣습니다.
        edit_df = summary_df.copy()
        edit_df.insert(0, "상세", "상세보기")
        edit_df.insert(0, "선택", bool(st.session_state.get(f"{key}_select_all", False)))
        # 아직 택배사를 안 고른 주문은 설정 화면에서 정한 기본 택배사(기본값 CJ대한통운)로
        # 미리 채워둡니다. 그대로 두면 그 택배사로 등록되고, 다르면 골라서 바꾸면 됩니다.
        default_courier_label = courier_label(settings.get_default_courier_code())
        edit_df["택배사"] = [
            courier_label(order["delivery_company_code"]) if order["delivery_company_code"] else default_courier_label
            for order in sorted_orders
        ]
        # 송장번호는 반드시 '문자'로 다룹니다. (숫자로 두면 표가 float로 바꿔버려
        # 앞자리 0이 사라지거나 소수점이 붙는 문제가 생깁니다)
        edit_df["송장번호"] = pd.Series(
            [str(order["invoice_number"]) if order["invoice_number"] else "" for order in sorted_orders],
            dtype="string",
        )
        # "퀵스타" 칸은 줄마다 누를 수 있는 버튼으로 만듭니다. 이미 접수한 주문은
        # 실수로 두 번 접수하지 않도록 신청번호를 보여줍니다.
        edit_df["퀵스타"] = [
            f"접수됨 {order['quickstar_order_no']}" if order.get("quickstar_order_no") else "퀵스타연동"
            for order in sorted_orders
        ]

        def _handle_detail_click():
            click = st.session_state.get(f"{key}_detail_click")
            if click is not None:
                st.session_state[f"{key}_detail_row"] = click["row"]

        def _handle_quickstar_click():
            click = st.session_state.get(f"{key}_quickstar_click")
            if click is not None:
                st.session_state[f"{key}_quickstar_row"] = click["row"]

        editable_columns = {"선택", "택배사", "송장번호"}
        edited = st.data_editor(
            edit_df,
            hide_index=True,
            key=f"{key}_editor",
            height=table_height,
            row_height=TABLE_ROW_HEIGHT,
            width="stretch",
            disabled=[col for col in edit_df.columns if col not in editable_columns],
            column_config={
                "선택": st.column_config.CheckboxColumn("선택", help="일괄 작업(관세청 검증 등)을 할 행을 체크하세요."),
                "상세": st.column_config.ButtonColumn(
                    "상세",
                    type="tertiary",
                    on_click=_handle_detail_click,
                    key=f"{key}_detail_click",
                    help="누르면 오른쪽에 이 주문의 상세정보가 나타납니다.",
                ),
                "퀵스타": st.column_config.ButtonColumn(
                    "퀵스타",
                    type="tertiary",
                    on_click=_handle_quickstar_click,
                    key=f"{key}_quickstar_click",
                    help="누르면 이 주문의 수취인 정보를 퀵스타 배대지에 접수합니다 (확인 창을 거칩니다).",
                ),
                "택배사": st.column_config.SelectboxColumn(
                    "택배사",
                    options=[COURIER_UNSET_LABEL] + list(COURIER_LABEL_TO_CODE.keys()),
                    required=True,
                    help="택배사를 고르세요. (설정 화면에서 정한 기본 택배사가 미리 선택되어 있습니다)",
                ),
                "송장번호": st.column_config.TextColumn("송장번호", help="송장번호를 입력하세요."),
            },
        )
        st.caption(f"총 {len(sorted_rows)}건")

    checked_orders = [
        order for order, checked in zip(sorted_orders, edited["선택"].tolist()) if checked
    ]
    # "상세보기" 버튼을 누른 행이 있으면 그 주문의 상세정보를 보여줍니다.
    # (has_detail/detail_row는 화면을 좌우로 나눌지 정하려고 위에서 이미 계산했습니다)
    detail_orders = [sorted_orders[detail_row]] if has_detail else []

    # "퀵스타" 버튼을 누른 행은, 정렬된 순서 기준의 줄번호라서 여기(정렬을 아는 곳)에서
    # 실제 주문으로 바꿔서 넘겨줍니다. 화면 쪽에서는 주문 id로 찾아 쓰면 됩니다.
    quickstar_row = st.session_state.pop(f"{key}_quickstar_row", None)
    if quickstar_row is not None and 0 <= quickstar_row < len(sorted_orders):
        st.session_state[f"{key}_quickstar_order_id"] = sorted_orders[quickstar_row]["id"]

    edited_rows = [
        {
            "order": order,
            "택배사코드": COURIER_LABEL_TO_CODE.get(courier_value),
            "송장번호": _invoice_to_str(invoice_value),
        }
        for order, courier_value, invoice_value in zip(
            sorted_orders, edited["택배사"].tolist(), edited["송장번호"].tolist()
        )
    ]
    return col_detail, checked_orders, detail_orders, edited_rows


def render_records_table(rows: list, key: str, caption: str = None) -> None:
    """
    주문이 아닌 목록(취소/반품/교환 등)을 발송대기 표와 똑같은 방식으로 보여줍니다.
      - 표시할 항목·순서 바꾸기 / 정렬 기준·방향 / 표 너비·높이 조절 (모두 저장됨)
      - 행을 클릭하면 오른쪽에 그 건의 전체 내용이 나타나고, ✕닫기로 닫습니다
        (상세정보가 없을 땐 표가 옆으로 꽉 차고, 열었을 때만 화면을 나눕니다)

    rows: 화면에 보여줄 딕셔너리들의 목록 (모두 같은 항목(키)을 가져야 합니다)
    key : 설정 저장/위젯 구분용 고유 키 (화면마다 다르게 줍니다)
    """
    if not rows:
        return
    all_columns = list(rows[0].keys())

    # ---- 표시할 항목과 그 순서 (직접 정하면 저장되어 재시작해도 유지됩니다) ----
    saved_order_text = settings_repository.get_setting(f"column_order:{key}")
    if saved_order_text:
        saved_columns = [col for col in saved_order_text.split("|") if col in all_columns]
    else:
        saved_columns = all_columns

    with st.expander("표시할 항목·순서 바꾸기", expanded=False):
        st.caption(
            "보고 싶은 항목만 골라서, 고른 순서대로 표에 나옵니다. "
            "여기서 정한 순서는 저장되어 프로그램을 다시 켜도 그대로 유지됩니다."
        )
        summary_columns = st.multiselect(
            "표시할 항목 (고른 순서대로 표시)",
            options=all_columns,
            default=saved_columns,
            key=f"{key}_column_order",
        )
        if st.button("기본 순서로 되돌리기", key=f"{key}_reset_columns"):
            settings_repository.set_setting(f"column_order:{key}", "|".join(all_columns))
            st.session_state.pop(f"{key}_column_order", None)
            st.rerun()

    if not summary_columns:
        summary_columns = all_columns
    if summary_columns != saved_columns:
        settings_repository.set_setting(f"column_order:{key}", "|".join(summary_columns))

    # ---- 정렬 기준/방향, 표 너비 (모두 저장됩니다) ----
    saved_sort_col = settings_repository.get_setting(f"sort_col:{key}", all_columns[0])
    saved_sort_dir = settings_repository.get_setting(f"sort_dir:{key}", "오름차순")
    if saved_sort_col not in summary_columns:
        saved_sort_col = summary_columns[0]
    saved_width_percent = min(int(settings_repository.get_setting(f"table_width:{key}", "95")), 95)

    col_sort_col, col_sort_dir, col_width = st.columns([2, 1, 2])
    with col_sort_col:
        sort_col = st.selectbox(
            "정렬 기준", options=summary_columns, index=summary_columns.index(saved_sort_col), key=f"{key}_sort_col"
        )
    with col_sort_dir:
        sort_dir = st.selectbox(
            "정렬 방향", options=["오름차순", "내림차순"],
            index=0 if saved_sort_dir == "오름차순" else 1, key=f"{key}_sort_dir",
        )
    with col_width:
        st.write("")
        _detail_width = st.slider(
            "상세내역 너비(%)", min_value=10, max_value=70, value=min(max(100 - saved_width_percent, 10), 70), step=5,
            key=f"{key}_width",
            help="오른쪽 상세내역이 차지하는 가로 비율입니다. 높일수록 상세가 넓어집니다. (예: 60 → 상세가 60%)",
        )
        table_width_percent = 100 - _detail_width

    # ★정렬은 항상 저장(기본값이어도): 표가 이 값을 읽어 머리글 정렬 화살표를 그립니다.
    if settings_repository.get_setting(f"sort_col:{key}") != sort_col:
        settings_repository.set_setting(f"sort_col:{key}", sort_col)
    if settings_repository.get_setting(f"sort_dir:{key}") != sort_dir:
        settings_repository.set_setting(f"sort_dir:{key}", sort_dir)
    if table_width_percent != saved_width_percent:
        settings_repository.set_setting(f"table_width:{key}", str(table_width_percent))

    sorted_rows = sorted(rows, key=lambda r: str(r.get(sort_col) or ""), reverse=(sort_dir == "내림차순"))

    # ---- AG-Grid 표(왼쪽) + 상세(오른쪽 옆), fragment로 스크롤 안 튀게 ----
    # 레코드 목록에는 'No' 컬럼이 없을 수 있어, 체크박스가 달리도록 맨 앞에 번호를 붙입니다.
    st.caption(
        (caption or "표 왼쪽 체크박스를 체크하면 오른쪽 옆에 상세내용이 나타납니다.")
        + " 맨 위 머리글 체크박스로 전체선택할 수 있고, 컬럼(항목)은 마우스로 끌어 순서를 바꿀 수 있으며 자동 저장됩니다."
    )
    grid_rows = []
    for i, r in enumerate(sorted_rows):
        row = {"No": i + 1}
        for c in summary_columns:
            row[c] = r.get(c)
        grid_rows.append(row)
    grid_df = pd.DataFrame(grid_rows) if grid_rows else pd.DataFrame(columns=["No"] + list(summary_columns))

    def _record_detail(detail):
        st.markdown("**상세내용**")
        detail_df = pd.DataFrame(
            [(k, str(v)) for k, v in detail.items()], columns=["항목", "값"]
        ).set_index("항목")
        st.table(detail_df)
        # 이 건의 '원주문 상세내역'(고객·상품·주소·연락처 등)도 함께 — 발송대기 상세처럼.
        moid = detail.get("주문번호") or detail.get("마켓주문번호")
        if moid and str(moid) not in ("", "-"):
            order = order_repository.get_full_order_by_market_id(moid)
            if order:
                # CS메모는 잘 보이게 상세 '맨 위'에 둡니다(취소·반품·반품완료·교환 공통).
                # 아래 '주문 상세내역'에서는 with_cs_memo=False로 중복 편집기를 막습니다.
                st.divider()
                render_cs_memo_editor(order)
                st.markdown("**주문 상세내역**")
                render_full_detail(order, reveal=True, with_cs_memo=False)
            else:
                st.caption("이 주문의 상세내역은 아직 앱에 수집되지 않았습니다. (해당 주문 단계에서 수집하면 보입니다)")

    render_grid_detail(
        grid_df, key=key, orders=sorted_rows, detail_renderer=_record_detail,
        empty_caption="표 왼쪽 체크박스를 체크하면 여기에 그 건의 상세내용이 나타납니다.",
        height=480,
    )


# 쿠팡 취소/반품 처리상태(receiptStatus) → 보기 좋은 한글. (실데이터 기준)
CLAIM_STATUS_LABELS = {
    "RELEASE_STOP_UNCHECKED": "출고중지요청",
    "RETURNS_UNCHECKED": "반품접수",
    "VENDOR_WAREHOUSE_CONFIRM": "입고완료",
    "REQUEST_COUPANG_CHECK": "쿠팡확인요청",
    "RETURNS_COMPLETED": "완료",
}
# '완료(종결)'된 것으로 보고, '접수 건만 보기'에서 숨길 상태값.
CLAIM_COMPLETED_STATUSES = {"RETURNS_COMPLETED"}

# 쿠팡 출고중지 처리상태(releaseStopStatus) 중 '아직 처리 안 됨(판매자 액션 필요)' 값.
# ★중요: 쿠팡은 이런 건의 receiptStatus를 즉시 'RETURNS_COMPLETED'(완료)로 내려주지만,
#   releaseStopStatus는 '미처리'로 남습니다. Wing '출고중지관리'는 이 값으로 집계하므로,
#   우리도 이 값이 '미처리'면 '진행 중(출고중지요청)'으로 취급해야 집계가 맞습니다.
RELEASE_STOP_PENDING = "미처리"


def _is_release_stop_pending(claim: dict) -> bool:
    """아직 처리 안 된 출고중지요청(판매자가 출고중지완료/이미출고 처리해야 하는 건)."""
    return (claim.get("release_stop_status") or "") == RELEASE_STOP_PENDING


def _is_release_stop(claim: dict) -> bool:
    """'출고중지요청'(사실상 '취소') 여부 → 반품이 아니라 취소로 분류합니다.
    미처리 출고중지(releaseStopStatus=미처리) 또는 옛 RELEASE_STOP_* 상태를 포함합니다."""
    return _is_release_stop_pending(claim) or (claim.get("receipt_status") or "").startswith("RELEASE_STOP")


def _claim_done_date(claim):
    """완료일시(complete_confirm_date) 우선, 없으면 접수일시(requested_at)로 날짜를 뽑습니다."""
    text = (claim.get("complete_confirm_date") or claim.get("requested_at") or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def render_claim_list(claim_type: str, title: str, empty_message: str,
                      default_hide_completed: bool = False,
                      only_completed: bool = False) -> None:
    """
    취소주문/반품주문 화면 공용 렌더러입니다. 둘 다 쿠팡의 같은 API
    (returnRequests, cancelType으로만 구분)를 쓰기 때문에 화면 구조도
    똑같습니다.
    default_hide_completed=True 이면 '완료' 건을 숨기고 현재 접수(진행 중)만 기본 표시합니다.
    only_completed=True 이면 반대로 '완료(RETURNS_COMPLETED)'된 건만 보여줍니다
    (반품완료/환불완료 화면용). 기간은 '완료일시' 기준으로 거릅니다.
    """
    settings_key = f"claim:{claim_type}"

    _date_label = "완료일시" if only_completed else "접수일시"
    period_from, period_to = render_period_picker(claim_type, f"{_date_label}(시작)", f"{_date_label}(종료)")
    collect_clicked = st.button("수집하기", type="primary", width="stretch", key=f"{claim_type}_collect")

    if collect_clicked:
        # ★수집은 표시 필터와 무관하게 '항상 오늘까지' 가져옵니다(새로 접수된 건 안 놓치게).
        collect_to = date.today()
        collect_from = min(period_from, collect_to)
        with st.spinner(f"쿠팡에서 {title}을(를) 가져오는 중입니다..."):
            result = claims_sync_service.sync_claims(claim_type, collect_from, collect_to)
        if result["status"] == "fail":
            st.error(f"수집 실패: {result['error_message']}")
        else:
            _recon = result.get("reconciled_count") or 0
            _recon_msg = f", 정리 {_recon}건" if _recon else ""
            st.success(
                f"수집 완료 - 전체 {result['fetched_count']}건 "
                f"(신규 {result['new_count']}건, 갱신 {result['updated_count']}건{_recon_msg}, "
                f"오류 {result['error_count']}건)"
            )
            if result["error_count"]:
                # 부분 실패 시에는 rerun하지 않고 안내를 남깁니다(rerun하면 경고가 사라져서).
                st.warning(
                    "일부 상점 조회가 실패했습니다(일시적 쿠팡 서버 오류일 수 있음). "
                    "잠시 후 '수집하기'를 한 번 더 누르면 누락분이 채워집니다.\n\n"
                    f"{result.get('error_message') or ''}"
                )
            else:
                st.rerun()

    # ★'출고중지요청'(RELEASE_STOP)은 실제로는 '취소'라서 재분류합니다.
    #   반품주문 = 반품 클레임에서 출고중지 '제외' / 취소주문 = 취소 클레임 + 출고중지(반품으로 저장된 것 '포함').
    if claim_type == "RETURN":
        claims = [c for c in claims_repository.list_claims("RETURN") if not _is_release_stop(c)]
    else:
        claims = claims_repository.list_claims("CANCEL") + [
            c for c in claims_repository.list_claims("RETURN") if _is_release_stop(c)
        ]

    def _req_date(claim):
        text = (claim.get("requested_at") or "")[:10]
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    # ★'현재 접수(진행 중)' = 완료 아님 + 시작일 이후(오늘까지) → 아래 표(현재 접수 건만 보기)와 '같은 기준'.
    #   상한은 종료일이 과거로 고정돼 있어도 '오늘'까지 포함(오늘 새로 접수된 건 안 놓치게).
    _upper = max(period_to, date.today())
    # 완료만 보기(반품완료 화면): 완료된 건 중 '완료일시'가 기간 안인 것.
    completed_claims = [
        c for c in claims
        if (c.get("receipt_status") or "") in CLAIM_COMPLETED_STATUSES
        and (_d := _claim_done_date(c)) is not None and period_from <= _d <= _upper
    ]
    pending_claims = [
        c for c in claims
        if ((c.get("receipt_status") or "") not in CLAIM_COMPLETED_STATUSES or _is_release_stop_pending(c))
        and (_d := _req_date(c)) is not None and period_from <= _d <= _upper
    ]
    last_sync_at = claims_sync_service.get_last_sync_at(settings_key)
    if last_sync_at:
        if only_completed:
            st.info(
                f"**완료된 {title}: {len(completed_claims)}건** "
                f"(완료일시 {period_from}~오늘 기준, 확인 시각: {last_sync_at})"
            )
        else:
            st.info(
                f"**현재 접수(진행 중) {title}: {len(pending_claims)}건** "
                f"({period_from}~오늘 기준, 확인 시각: {last_sync_at})"
            )
    else:
        st.caption("아직 한 번도 수집하지 않았습니다.")

    st.divider()

    if not claims:
        st.info(empty_message)
        return

    # 🛑 출고중지요청 — 취소주문 화면에서만. 쿠팡 '출고중지완료 처리' API로 실제 처리합니다.
    # (PUT .../returnRequests/{receiptId}/stoppedShipment — Wing '출고중지완료' 버튼과 동일 동작)
    if claim_type == "CANCEL":
        stop_items = [c for c in pending_claims if _is_release_stop(c)]
        if stop_items:
            by_receipt = {str(c["receipt_id"]): c for c in stop_items}

            def _fmt_stop(rid):
                c = by_receipt[rid]
                return (f"접수 {rid} · 주문 {c.get('market_order_id')} · "
                        f"{c.get('reason_category1') or '-'} · {(c.get('requested_at') or '')[:10]}")

            st.markdown(f"##### 🛑 출고중지요청 — 출고중지완료 처리 (진행 중 {len(stop_items)}건)")
            st.caption(
                "처리할 건을 고른 뒤 버튼을 누르면, **선택한 건만** 쿠팡에서 **실제로 출고중지완료** "
                "처리됩니다(발송하지 않고 취소 확정). 기본은 전체 선택입니다."
            )
            picked = st.multiselect(
                "출고중지완료 처리할 건",
                options=list(by_receipt.keys()),
                default=list(by_receipt.keys()),
                format_func=_fmt_stop,
                key=f"{claim_type}_stop_pick",
            )
            if st.button(
                f"✅ 선택한 {len(picked)}건 출고중지완료 처리",
                type="primary", disabled=not picked, key=f"{claim_type}_stop_complete",
            ):
                ok = 0
                fails = []
                with st.spinner(f"쿠팡에 출고중지완료 처리 중... ({len(picked)}건)"):
                    for rid in picked:
                        res = claims_sync_service.complete_release_stop(by_receipt[rid])
                        if res.get("succeeded"):
                            ok += 1
                        else:
                            fails.append(f"접수 {rid}: {res.get('message')}")
                if ok:
                    st.success(f"{ok}건 출고중지완료 처리됨")
                if fails:
                    st.error("일부 실패:\n" + "\n".join(f"- {x}" for x in fails))
                if ok and not fails:
                    st.rerun()
            st.divider()

    _c_search, _c_filter = st.columns([3, 1])
    with _c_search:
        keyword = st.text_input(f"{title} 검색", placeholder="주문번호, 접수번호, 사유 등으로 검색", key=f"{claim_type}_search")
    with _c_filter:
        st.write("")
        if only_completed:
            period_only = st.checkbox(
                "기간 안 완료만 보기", value=True, key=f"{claim_type}_period_done",
                help="선택한 '완료일시' 기간 안에 완료된 반품만 보여줍니다. "
                     "(끄면 수집된 완료 건 전체가 보입니다)",
            )
        else:
            hide_completed = st.checkbox(
                "현재 접수 건만 보기", value=default_hide_completed, key=f"{claim_type}_hide_done",
                help="완료된 건과 선택한 '접수일시' 기간을 벗어난 예전 건을 빼고, "
                     "지금 접수·진행 중인 건만 보여줍니다. (끄면 과거 전체가 보입니다)",
            )

    if only_completed:
        # 반품완료 화면: 완료된 건만. 기본은 '기간 안 완료'(배너 숫자와 일치), 끄면 완료 전체.
        if period_only:
            claims = completed_claims
        else:
            claims = [c for c in claims if (c.get("receipt_status") or "") in CLAIM_COMPLETED_STATUSES]
        if not claims:
            st.info(
                "표시할 완료된 반품이 없습니다. 위 '수집하기'를 누르거나, "
                "기간을 넓히거나 '기간 안 완료만 보기'를 꺼보세요."
            )
            return
    # '현재 접수 건만' — 위에서 이미 계산한 pending_claims(완료 제외 + 기간 안) 그대로 사용 → 배너 숫자와 표가 일치.
    elif hide_completed:
        claims = pending_claims
        if not claims:
            st.info(
                "선택한 기간에 현재 접수(진행 중)인 건이 없습니다. "
                "기간을 넓히거나 '현재 접수 건만 보기'를 끄면 과거·완료 건도 볼 수 있습니다."
            )
            return

    rows = [
        {
            "접수번호": c["receipt_id"],
            "주문번호": c["market_order_id"] or "-",
            "상점": c.get("market_account_name") or "-",
            "처리상태": (
                "출고중지요청(미처리)" if _is_release_stop_pending(c)
                else CLAIM_STATUS_LABELS.get(c["receipt_status"], c["receipt_status"] or "-")
            ),
            "출고중지": c.get("release_stop_status") or "-",
            "사유": c["reason_category1"] or "-",
            "상세사유분류": c["reason_category2"] or "-",
            "상세사유": c["reason_detail"] or "-",
            "접수일시": c["requested_at"] or "-",
            "완료구분": c["complete_confirm_type"] or "-",
            "완료일시": c["complete_confirm_date"] or "-",
        }
        for c in claims
    ]

    filtered = [row for row in rows if matches_search(row, keyword)]

    if not filtered:
        st.info("검색 결과가 없습니다.")
        return

    # 발송대기와 같은 구성(항목순서·정렬·너비·높이 조절 + 행 클릭 상세패널)으로 보여줍니다.
    render_records_table(filtered, key=f"claim_table:{claim_type}")


# ==========================================================
# 상단 알람 배너 (새 신규주문/취소/반품/CS 감지 → 배너 + 알림음)
# ----------------------------------------------------------
# 샵마인처럼 새 건이 들어오면 화면 맨 위에 눈에 띄는 배너로 알려주고, 소리로도
# 알립니다. 실제 감지·수집 로직은 services/alarm_service.py 에 있습니다.
# ==========================================================

_ALARM_WAV_CACHE = None


def _alarm_wav_bytes() -> bytes:
    """알림음(짧은 '딩동' 2음)을 메모리에서 WAV로 만들어 돌려줍니다(1회 생성 후 캐시)."""
    global _ALARM_WAV_CACHE
    if _ALARM_WAV_CACHE is not None:
        return _ALARM_WAV_CACHE
    import math
    import struct
    import wave

    sample_rate = 44100

    def tone(freq: float, dur: float, vol: float = 0.5) -> bytes:
        total = int(sample_rate * dur)
        edge = max(1, int(0.012 * sample_rate))  # 클릭음 방지용 페이드
        out = bytearray()
        for i in range(total):
            env = min(1.0, i / edge, (total - i) / edge)
            sample = int(vol * env * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
            out += struct.pack("<h", sample)
        return bytes(out)

    gap = b"\x00\x00" * int(sample_rate * 0.05)
    frames = tone(880.0, 0.14) + gap + tone(1174.7, 0.18)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(frames)
    _ALARM_WAV_CACHE = buffer.getvalue()
    return _ALARM_WAV_CACHE


def _play_alarm_sound() -> None:
    """알림음을 한 번 재생합니다(보이지 않는 오디오 위젯)."""
    st.markdown(
        "<style>[class*='st-key-alarm_sound_slot']{position:absolute;width:1px;height:1px;"
        "opacity:0;overflow:hidden;pointer-events:none;}</style>",
        unsafe_allow_html=True,
    )
    with st.container(key="alarm_sound_slot"):
        st.audio(_alarm_wav_bytes(), format="audio/wav", autoplay=True)


def render_alarm_bar() -> None:
    """화면 맨 위 알람 배너. app.py에서 각 화면을 그리기 전에 한 번 호출합니다."""
    from services import alarm_service

    if not alarm_service.is_enabled():
        return

    # 앱(서버)이 떠 있는 동안 주기적으로 새 건을 조용히 재수집하는 폴러를 띄웁니다.
    alarm_service.ensure_poller_started()
    # 배너 자체는 주기적으로 새로고침되어 새 건수를 반영합니다.
    _alarm_bar_fragment()


@st.fragment(run_every=30)
def _alarm_bar_fragment() -> None:
    from services import alarm_service

    counts = alarm_service.count_unseen()
    total = sum(counts.values())

    # 알림음: 이 세션에서 '이전보다 새 건이 늘었을 때'만 울립니다.
    #   (앱을 처음 열었을 때는 조용히 현재값만 기억하고, 그 뒤 증가분에만 소리)
    prev = st.session_state.get("_alarm_total_seen")
    if prev is None:
        st.session_state["_alarm_total_seen"] = total
    else:
        if total > prev and alarm_service.is_sound_enabled():
            _play_alarm_sound()
        st.session_state["_alarm_total_seen"] = total

    if total <= 0:
        return

    parts = [
        f"{alarm_service.ALARM_LABELS[key]} <b>{counts[key]}</b>"
        for key in alarm_service.ALARM_KEYS
        if counts.get(key, 0) > 0
    ]
    summary = " &nbsp;·&nbsp; ".join(parts)
    st.markdown(
        f"""
        <div style="background:#FDECEC;border:1px solid #F1AEAE;border-left:5px solid #D32F2F;
                    border-radius:8px;padding:9px 14px;margin-bottom:6px;color:#B71C1C;
                    font-size:0.98rem;">
            🔔 <b>새 알림</b> &nbsp; {summary}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 종류별 '보기' 버튼은 아래 탭(반품주문·CS메모관리 등)과 기능이 겹쳐서 없앴습니다.
    # 여기서는 알림을 지우는 '모두 확인'만 둡니다.
    if st.button("모두 확인", key="alarm_ack_all", width="stretch"):
        alarm_service.acknowledge()
        st.session_state["_alarm_total_seen"] = 0
        st.rerun(scope="app")
