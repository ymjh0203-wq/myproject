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
    "직접 지정", "오늘", "어제", "이번주",
    "1주전", "2주전", "3주전", "4주전", "5주전", "6주전", "7주전", "8주전", "9주전", "10주전",
    "이번달", "전달", "전전달", "올해", "작년", "전전년",
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


def _quick_period_to_range(label: str):
    """빠른 선택 항목을 (시작일, 종료일)로 바꿉니다. '직접 지정'이면 None을 돌려줍니다."""
    if label == "오늘":
        today = date.today()
        return today, today
    if label == "어제":
        yesterday = date.today() - timedelta(days=1)
        return yesterday, yesterday
    if label == "이번주":
        return _week_range(0)
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
    applied_key = f"{key_prefix}_quick_period_applied"

    # Streamlit은 "value="와 session_state 직접 지정을 같이 쓰면 경고를 내므로,
    # 처음 한 번만 value 역할로 session_state에 기본값을 넣어두고, 위젯 생성 시에는
    # value=를 넘기지 않습니다.
    if from_key not in st.session_state:
        st.session_state[from_key] = date.today() - timedelta(days=default_days)
    if to_key not in st.session_state:
        st.session_state[to_key] = date.today()

    col_quick, col_from, col_to = st.columns([1, 1, 1])
    with col_quick:
        quick = st.selectbox("빠른 선택", options=QUICK_PERIOD_OPTIONS, key=f"{key_prefix}_quick_period")

    quick_range = _quick_period_to_range(quick)
    # 빠른 선택이 "방금 바뀐" 경우에만 시작일/종료일을 덮어씁니다. 그래야 사용자가
    # 시작일/종료일을 손으로 미세조정한 뒤에도, 다시 렌더링될 때 값이 되돌아가지 않습니다.
    if quick_range and st.session_state.get(applied_key) != quick:
        st.session_state[from_key] = quick_range[0]
        st.session_state[to_key] = quick_range[1]
        st.session_state[applied_key] = quick
    elif not quick_range:
        st.session_state[applied_key] = quick

    with col_from:
        period_from = st.date_input(label_from, key=from_key)
    with col_to:
        period_to = st.date_input(label_to, key=to_key)

    return period_from, period_to

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


def build_excel_bytes(rows: list) -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame(rows).to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


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

    # 쿠팡 마켓수수료는 12%로 일괄 계산합니다(판매자 방침).
    # 마켓수수료금액 = 판매금액 × 12%, 정산예상금액 = 판매금액 − 수수료(= 판매금액 × 88%)
    market_fee = round(sales_total * COUPANG_FEE_RATE)
    settlement_estimate = sales_total - market_fee

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
    invoice_display = (
        _blank_if_none_sentinel(order["invoice_number"])
        or _blank_if_none_sentinel(order.get("market_invoice_number"))
        or "-"
    )

    remote_area = order.get("remote_area")
    remote_area_display = "-" if remote_area is None else ("예" if remote_area else "아니오")

    return {
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
        "정산추가정보": "-",
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


def render_full_detail(order: dict, reveal: bool) -> None:
    """
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


def _render_table_with_fragment(key, summary_columns, sorted_rows, sorted_orders, table_width_percent, detail_renderer, multi_select=False):
    """
    표 + 상세를 st.fragment(부분 새로고침)로 함께 그립니다. 행을 클릭하면 화면 전체가
    아니라 이 조각만 다시 그려져서, 스크롤이 맨 위로 튀지 않고 상세가 제자리에서 바뀝니다.
    반환: (None, 선택 주문 목록, 상세 주문 목록, None) — render_full_table와 호환.
    """
    summary_df = pd.DataFrame(sorted_rows)[summary_columns] if sorted_rows else pd.DataFrame(columns=summary_columns)
    fitted_height = TABLE_ROW_HEIGHT * (len(sorted_rows) + 1) + 3
    default_height = max(100, min(fitted_height, 600))

    # 상세 칸을 '항상' 2칸 고정 → 체크/해제해도 화면이 위로 안 튐.
    detail_percent = max(30, 100 - table_width_percent)
    col_table, col_detail = st.columns([100 - detail_percent, detail_percent])

    with col_table:
        st.caption(
            "표 왼쪽 체크박스를 체크하면 그 주문 상세가 오른쪽에 나타납니다. "
            "맨 위 머리글 체크박스로 전체선택할 수 있고, 체크한 주문들로 일괄작업(이동·검증)을 합니다."
        )
        height = st.slider("표 높이 조절", 100, 3000, default_height, 50, key=f"{key}_height")
        event = st.dataframe(
            summary_df,
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            key=f"{key}_table",
            height=height,
            row_height=TABLE_ROW_HEIGHT,
            width="stretch",
        )
        st.caption(f"총 {len(sorted_rows)}건")

    picked_idx = list(event.selection.rows)
    selected = [sorted_orders[i] for i in picked_idx if 0 <= i < len(sorted_orders)]

    with col_detail:
        if selected:
            # 체크한 주문(여럿이면 마지막)의 상세를 보여줍니다. 체크만 하면 바로 뜹니다.
            detail_renderer(selected[-1])
        else:
            st.caption("표 왼쪽 체크박스를 체크하면 여기에 그 주문 상세가 나타납니다.")

    return None, selected, selected, None


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
        table_width_percent = st.slider(
            "표 너비 조절", min_value=30, max_value=95, value=saved_width_percent, step=5, key=f"{key}_width"
        )

    if sort_col != saved_sort_col:
        settings_repository.set_setting(f"sort_col:{key}", sort_col)
    if table_width_percent != saved_width_percent:
        settings_repository.set_setting(f"table_width:{key}", str(table_width_percent))
    if sort_dir != saved_sort_dir:
        settings_repository.set_setting(f"sort_dir:{key}", sort_dir)

    paired = list(zip(filtered_rows, filtered_orders))
    paired.sort(key=lambda pair: pair[0].get(sort_col), reverse=(sort_dir == "내림차순"))
    sorted_rows = [pair[0] for pair in paired]
    sorted_orders = [pair[1] for pair in paired]

    # 상세 렌더 함수가 주어지면, 표+상세를 fragment(부분 새로고침)로 그립니다.
    # (행 클릭 시 화면 전체가 아니라 그 부분만 갱신 → 스크롤이 위로 튀지 않음)
    if detail_renderer is not None and not invoice_editable:
        return _render_table_with_fragment(
            key, summary_columns, sorted_rows, sorted_orders, table_width_percent, detail_renderer, multi_select
        )

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
        table_width_percent = st.slider(
            "표 너비 조절", min_value=30, max_value=95, value=saved_width_percent, step=5, key=f"{key}_width"
        )

    if sort_col != saved_sort_col:
        settings_repository.set_setting(f"sort_col:{key}", sort_col)
    if table_width_percent != saved_width_percent:
        settings_repository.set_setting(f"table_width:{key}", str(table_width_percent))
    if sort_dir != saved_sort_dir:
        settings_repository.set_setting(f"sort_dir:{key}", sort_dir)

    sorted_rows = sorted(rows, key=lambda r: str(r.get(sort_col) or ""), reverse=(sort_dir == "내림차순"))

    # ---- 상세정보를 지금 보여줄 상태인지 판단 (발송대기와 같은 방식) ----
    detail_row = None
    table_state = st.session_state.get(f"{key}_table")
    if table_state is None:
        has_detail = False
    else:
        try:
            selected_rows = table_state["selection"]["rows"]
            has_detail = len(selected_rows) == 1
            if has_detail:
                detail_row = selected_rows[0]
        except Exception:
            has_detail = False
    # 목록이 줄어들어 선택 번호가 범위를 벗어나면 무시합니다.
    if detail_row is not None and not (0 <= detail_row < len(sorted_rows)):
        detail_row = None
        has_detail = False

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
                st.session_state.pop(f"{key}_table", None)
                st.rerun()
            detail = sorted_rows[detail_row]
            st.markdown("**상세내용**")
            detail_df = pd.DataFrame(
                [(k, str(v)) for k, v in detail.items()], columns=["항목", "값"]
            ).set_index("항목")
            st.table(detail_df)

    with col_table:
        st.caption(caption or "행을 클릭하면 오른쪽에 상세내용이 나타납니다.")
        summary_df = pd.DataFrame(sorted_rows)[summary_columns]
        fitted_height = TABLE_ROW_HEIGHT * (len(sorted_rows) + 1) + 3
        default_height = max(100, min(fitted_height, 600))
        table_height = st.slider(
            "표 높이 조절", min_value=100, max_value=3000, value=default_height, step=50, key=f"{key}_height"
        )
        st.dataframe(
            summary_df,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key=f"{key}_table",
            height=table_height,
            row_height=TABLE_ROW_HEIGHT,
            width="stretch",
        )
        st.caption(f"총 {len(sorted_rows)}건")


def render_claim_list(claim_type: str, title: str, empty_message: str) -> None:
    """
    취소주문/반품주문 화면 공용 렌더러입니다. 둘 다 쿠팡의 같은 API
    (returnRequests, cancelType으로만 구분)를 쓰기 때문에 화면 구조도
    똑같습니다.
    """
    settings_key = f"claim:{claim_type}"

    period_from, period_to = render_period_picker(claim_type, "접수일시(시작)", "접수일시(종료)")
    collect_clicked = st.button("수집하기", type="primary", width="stretch", key=f"{claim_type}_collect")

    if collect_clicked:
        if period_from > period_to:
            st.error("시작일이 종료일보다 늦을 수 없습니다.")
        else:
            with st.spinner(f"쿠팡에서 {title}을(를) 가져오는 중입니다..."):
                result = claims_sync_service.sync_claims(claim_type, period_from, period_to)
            if result["status"] == "fail":
                st.error(f"수집 실패: {result['error_message']}")
            else:
                st.success(
                    f"수집 완료 - 전체 {result['fetched_count']}건 "
                    f"(신규 {result['new_count']}건, 갱신 {result['updated_count']}건, "
                    f"오류 {result['error_count']}건)"
                )
                st.rerun()

    last_sync_at = claims_sync_service.get_last_sync_at(settings_key)
    last_count = claims_sync_service.get_last_fetched_count(settings_key)
    if last_sync_at:
        st.info(f"**쿠팡 기준 현재 {title}: {last_count}건** (확인 시각: {last_sync_at})")
    else:
        st.caption("아직 한 번도 수집하지 않았습니다.")

    st.divider()

    claims = claims_repository.list_claims(claim_type)
    if not claims:
        st.info(empty_message)
        return

    keyword = st.text_input(f"{title} 검색", placeholder="주문번호, 접수번호, 사유 등으로 검색", key=f"{claim_type}_search")

    rows = [
        {
            "접수번호": c["receipt_id"],
            "주문번호": c["market_order_id"] or "-",
            "상점": c.get("market_account_name") or "-",
            "처리상태": c["receipt_status"] or "-",
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
