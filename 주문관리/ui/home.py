# ==========================================================
# 홈 화면 (ui/home.py)
# ----------------------------------------------------------
# 샵마인 홈 화면 구성(정산 안내 + 주문·클레임 현황 카드 + 배송 현황 카드 +
# 상점별 현황 표)을 참고해서 만들었습니다.
#
# "새로고침"을 누르면 실제로 쿠팡에 다시 물어봐서(sync_service), 지금
# 쿠팡 기준으로 각 단계에 몇 건이 있는지 가져옵니다. 카드에 보이는 숫자는
# "우리 DB에 누적된 전체 건수"가 아니라 "마지막으로 확인했을 때 쿠팡이
# 실시간으로 알려준 건수"입니다 (상단 탭의 숫자와는 다른 값입니다).
#
# 구매확정은 쿠팡이 판매자용 조회 API를 아직 확인 못 해서 실시간 확인이
# 안 되고, 우리 DB에 저장된 건수만 보여줍니다.
#
# 아직 실제 기능이 없는 부분: 취소요청/반품요청/교환요청/미수령신고/
# 긴급·문의/반품완료/정산관리/상품평관리는 해당 데이터 자체가 없어서
# 숫자를 지어내지 않고 "-"로 표시합니다.
# ==========================================================

import time
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from urllib.parse import quote

import models
from repositories import (
    claims_repository,
    exchange_repository,
    inquiry_repository,
    order_repository,
    settings_repository,
)
from services import claims_sync_service, sync_service
from ui import common, settings


def _status_pill(label: str, count, nav: str, bg: str, fg: str, dot: str) -> str:
    """샵마인 홈처럼 '색깔 줄 + 점 + 라벨 + 건수 + ›' 한 줄을 만듭니다.
    nav(이동할 화면 이름)가 있으면 클릭 시 그 화면으로 이동하는 링크가 됩니다."""
    inner = (
        f'<span style="display:flex;align-items:center;gap:8px;font-size:13px;color:{fg};">'
        f'<span style="width:8px;height:8px;border-radius:50%;background:{dot};display:inline-block;"></span>{label}</span>'
        f'<span style="font-size:13px;color:{fg};white-space:nowrap;"><b style="font-weight:600;">{count}건</b> &rsaquo;</span>'
    )
    style = (
        f'display:flex;align-items:center;justify-content:space-between;background:{bg};'
        f'border-radius:8px;padding:9px 12px;margin-bottom:8px;text-decoration:none;'
    )
    if nav:
        return f'<a href="?nav={quote(nav)}" target="_self" style="{style}">{inner}</a>'
    return f'<div style="{style}">{inner}</div>'


def _delivery_bar(label: str, count, nav: str, bg: str) -> str:
    """샵마인 배송 현황처럼 진한 색 바 한 줄(흰 글씨 + 건수 + ›)을 만듭니다."""
    cnt = "" if count is None else f'<b style="font-weight:600;">{count}건</b> '
    inner = (
        f'<span style="display:flex;align-items:center;gap:8px;font-size:14px;color:#fff;">'
        f'<span style="width:9px;height:9px;border-radius:50%;background:rgba(255,255,255,.85);display:inline-block;"></span>{label}</span>'
        f'<span style="font-size:13px;color:#fff;white-space:nowrap;">{cnt}&rsaquo;</span>'
    )
    style = (
        f'display:flex;align-items:center;justify-content:space-between;background:{bg};'
        f'border-radius:8px;padding:12px 14px;margin-bottom:10px;text-decoration:none;'
    )
    if nav:
        return f'<a href="?nav={quote(nav)}" target="_self" style="{style}">{inner}</a>'
    return f'<div style="{style}">{inner}</div>'

_PERIOD_DAYS = {"최근 7일": 7, "최근 14일": 14, "최근 30일": 30, "전체 기간(최근 90일로 조회)": 90}

_PLATFORM_DISPLAY = {"coupang": "쿠팡", "other": "기타"}


def _period_to_range(period_label: str):
    days = _PERIOD_DAYS.get(period_label, 30)
    return date.today() - timedelta(days=days), date.today()


# 각 단계(work_status) → 그 화면의 기간 설정 프리픽스(render_period_picker에서 쓴 것).
_STAGE_PREFIX = {
    models.WORK_STATUS_NEW: "new_orders",
    models.WORK_STATUS_READY_TO_SHIP: "rts",
    models.WORK_STATUS_SHIPPING: "shipping",
    models.WORK_STATUS_DELIVERED: "delivered",
}


def _tab_period(prefix: str, default_days: int = 30):
    """
    각 탭(화면)에 설정해둔(고정해둔) 조회 기간을 돌려줍니다. 홈 새로고침이 '각 탭에
    설정된 기간으로 단계별 수집'하도록 씁니다. 먼저 세션을 보고, 없으면 DB에 저장된
    고정값을 읽습니다(그 탭을 아직 안 열었어도 반영). 둘 다 없으면 최근 default_days일.
    """
    period_from = st.session_state.get(f"{prefix}_period_from")
    period_to = st.session_state.get(f"{prefix}_period_to")
    if not period_from:
        period_from = _parse_iso_date(settings_repository.get_setting(f"period_from:{prefix}") or "")
    if not period_to:
        period_to = _parse_iso_date(settings_repository.get_setting(f"period_to:{prefix}") or "")
    if period_from and period_to:
        return period_from, period_to
    return date.today() - timedelta(days=default_days), date.today()


def _parse_iso_date(text):
    try:
        return date.fromisoformat(text)
    except Exception:
        return None


def _sync_statuses(work_statuses: list) -> None:
    """홈 새로고침: 각 단계를 '그 탭에 설정된 기간'으로 수집합니다."""
    errors = []
    for work_status in work_statuses:
        period_from, period_to = _tab_period(_STAGE_PREFIX.get(work_status, ""))
        result = sync_service.sync_orders_for_stage(work_status, period_from, period_to)
        if result["status"] == "fail":
            errors.append(f"{work_status}: {result['error_message']}")
    if errors:
        st.session_state["home_sync_errors"] = errors


def _sync_everything() -> None:
    """
    신규주문/발송대기/배송중/배송완료 + 취소요청/반품요청/교환요청/상품문의/
    콜센터문의까지 전부 다시 수집합니다. (구매확정은 쿠팡 조회 API가 없어서
    제외합니다) 각 종류는 '그 탭에 설정된 기간'으로 수집합니다(각 탭 기간 존중).

    9종류를 하나씩 순서대로 하면 너무 오래 걸려서, 어느 정도는 동시에(병렬로)
    실행합니다. 단, 한꺼번에 다 동시에 보내면 쿠팡이 "API 호출 한도 초과"로
    거부해서(2026-07-21 확인), 동시에 최대 2개까지만 실행하고, 그마저도
    호출 한도 오류가 나면 잠깐 쉬었다가 한 번 더 시도합니다.
    """
    def P(prefix):
        return _tab_period(prefix)

    jobs = [
        ("신규주문", lambda: sync_service.sync_orders_for_stage(models.WORK_STATUS_NEW, *P("new_orders"))),
        ("발송대기", lambda: sync_service.sync_orders_for_stage(models.WORK_STATUS_READY_TO_SHIP, *P("rts"))),
        ("배송중", lambda: sync_service.sync_orders_for_stage(models.WORK_STATUS_SHIPPING, *P("shipping"))),
        ("배송완료", lambda: sync_service.sync_orders_for_stage(models.WORK_STATUS_DELIVERED, *P("delivered"))),
        ("취소요청", lambda: claims_sync_service.sync_claims("CANCEL", *P("CANCEL"))),
        ("반품요청", lambda: claims_sync_service.sync_claims("RETURN", *P("RETURN"))),
        ("교환요청", lambda: claims_sync_service.sync_exchange_requests(*P("exchange"))),
        ("상품문의", lambda: claims_sync_service.sync_product_inquiries(*P("cs_inq"))),
        # 취소·반품 등으로 쿠팡에서 사라진 발송대기/신규 주문을 정리합니다.(신규주문 기간 사용)
        ("사라진 주문 정리", lambda: sync_service.reconcile_active_orders(*P("new_orders"))),
    ]

    # 콜센터문의는 쿠팡 API가 계속 500 오류를 내서 기본으로 꺼져 있습니다.
    # (설정 > 수집 설정에서 켤 수 있습니다)
    if settings.is_call_center_sync_enabled():
        jobs.append(
            ("콜센터문의", lambda: claims_sync_service.sync_call_center_inquiries(*P("cs_inq")))
        )

    errors = []

    def _is_rate_limit_error(message: str) -> bool:
        return "호출 한도" in (message or "") or "429" in (message or "")

    def _run(job):
        """작업 하나를 실행하고, 호출 한도 오류면 한 번 쉬었다 다시 시도합니다."""
        for attempt in range(2):
            try:
                result = job()
            except Exception as error:  # 한 작업이 실패해도 나머지는 계속 진행합니다.
                return {"status": "fail", "error_message": str(error)}
            if result["status"] != "fail":
                return result
            if attempt == 0 and _is_rate_limit_error(result.get("error_message")):
                time.sleep(5)
                continue
            return result
        return {"status": "fail", "error_message": "알 수 없는 오류"}

    # 진행률(%)과 남은 시간을 진행바로 보여주면서 실행합니다.
    progress_jobs = [(label, (lambda j=job: _run(j))) for label, job in jobs]
    results = common.run_jobs_with_progress(progress_jobs, label="전체 새로고침 중")
    for label, result in results:
        if result.get("status") == "fail":
            # 콜센터문의는 쿠팡 API가 늘 500(internal error)을 내는 고질 문제라, 실패로
            # 표시하지 않습니다(원하면 설정에서 아예 끌 수 있음). 그 외 실패만 배너에 표시.
            if label == "콜센터문의":
                continue
            errors.append(f"{label}: {result.get('error_message')}")

    if errors:
        st.session_state["home_sync_errors"] = errors


def _render_live_metric(work_status: str, label: str) -> None:
    count = sync_service.get_last_fetched_count(work_status)
    if count is None:
        st.metric(label, "-")
    else:
        st.metric(label, count)


def _won(n) -> str:
    try:
        return f"{int(n):,}원"
    except (TypeError, ValueError):
        return "-"


def _render_dashboard() -> None:
    """맨 위 요약 대시보드: 오늘/이번 달 매출 + '지금 봐야 할 것'(점검 요약)."""
    today = date.today()
    today_str = today.isoformat()
    month = today_str[:7]

    # --- 오늘/이번 달 매출(결제일 기준) ---
    today_sales = today_cnt = month_sales = month_cnt = 0
    try:
        daily = order_repository.daily_sales_detailed(month, exclude_closed=True)
        for d in daily:
            month_sales += d["판매금액"]
            month_cnt += d["건수"]
            if d["일자"] == today_str:
                today_sales += d["판매금액"]
                today_cnt += d["건수"]
    except Exception:  # noqa: BLE001
        pass

    st.subheader("📊 오늘 한눈에")
    m = st.columns(4)
    m[0].metric("오늘 판매금액", _won(today_sales))
    m[1].metric("오늘 주문", f"{today_cnt:,}건")
    m[2].metric(f"{month} 판매금액", _won(month_sales))
    m[3].metric(f"{month} 주문", f"{month_cnt:,}건")

    # --- 지금 봐야 할 것(점검 요약) ---
    stuck_ship = pending_return = 0
    try:
        stuck_ship = len(order_repository.orders_stuck_in_status(models.WORK_STATUS_SHIPPING, 30))
    except Exception:  # noqa: BLE001
        pass
    try:
        for cl in claims_repository.list_claims("RETURN"):
            if (cl.get("receipt_status") or "") == "RETURNS_COMPLETED":
                continue
            req = (cl.get("requested_at") or "")[:10]
            try:
                if (today - date.fromisoformat(req)).days >= 7:
                    pending_return += 1
            except (TypeError, ValueError):
                pass
    except Exception:  # noqa: BLE001
        pass

    if stuck_ship or pending_return:
        bits = []
        if stuck_ship:
            bits.append(f"🚚 배송중 30일+ **{stuck_ship}건**")
        if pending_return:
            bits.append(f"↩️ 반품 미처리 7일+ **{pending_return}건**")
        st.warning("지금 봐야 할 것 — " + " · ".join(bits))
    else:
        st.success("지금 급히 봐야 할 막힌 주문은 없습니다. 👍")
    if st.button("🔎 점검판 열기", key="home_go_inspection"):
        st.session_state["current_view"] = "점검판"
        st.query_params["view"] = "점검판"
        st.rerun()

    st.divider()


def render() -> None:
    st.header("홈")

    if st.session_state.get("home_sync_errors"):
        errors = st.session_state.pop("home_sync_errors")
        st.error("일부 수집 실패:\n" + "\n".join(f"- {e}" for e in errors))

    if st.session_state.get("home_full_sync_done"):
        st.session_state.pop("home_full_sync_done")
        st.success(
            "신규주문·발송대기·배송중·배송완료·취소요청·반품요청·교환요청·상품문의를 "
            "다시 확인했습니다. (구매확정·콜센터문의는 쿠팡 API 문제로 제외)"
        )

    _render_dashboard()

    # ------------------------------------------------------
    # 상단 탭 숫자(신규주문/발송대기/배송중/배송완료)가 오래돼 보이는 원인은
    # 대부분 "해당 단계를 오랫동안 다시 수집 안 해서"입니다. 예를 들어
    # 배송중 화면을 한동안 안 열어보면, 실제로는 쿠팡에서 이미 배송완료된
    # 주문도 우리 DB에서는 계속 배송중으로 남아있습니다 (각 단계는 그
    # 화면에서 직접 수집해야만 다음 단계로 넘어가는지 확인하기 때문).
    # 이 버튼은 주문 4단계 + 취소/반품/교환/문의까지 전부 한 번에 다시
    # 수집해서 한꺼번에 최신화합니다. (구매확정만 API가 없어서 제외됩니다)
    # ------------------------------------------------------
    if st.button(
        "전체 한번에 새로고침 (각 탭 설정 기간으로 단계별 수집)", type="primary", width="stretch"
    ):
        # 진행바(%+남은시간)는 _sync_everything 안에서 보여줍니다.
        # 각 종류를 그 탭에 설정된 기간으로 수집합니다.
        _sync_everything()
        st.session_state["home_full_sync_done"] = True
        st.rerun()

    st.info(
        "**정산 안내**  \n"
        "표시된 정산 예상 금액은 쿠폰/포인트 등 프로모션 반영 여부에 따라 실제 정산 금액과 다를 수 있습니다.  \n"
        "정확한 금액은 쇼핑몰 판매자센터에서 확인해 주세요."
    )

    # 쿠팡 매출내역 조회로 '실제 정산금액'을 가져와 반영합니다(배송완료·구매확정 주문 대상).
    if st.session_state.get("home_settlement_result"):
        _sr = st.session_state.pop("home_settlement_result")
        if _sr.get("status") == "fail":
            st.error(f"정산내역 가져오기 실패: {_sr.get('error_message')}")
        else:
            _msg = f"쿠팡 실제 정산금액을 **{_sr['updated']}건** 반영했습니다 (조회 {_sr['fetched']}건)."
            if _sr.get("error_message"):
                st.warning(_msg + f"\n일부 오류: {_sr['error_message']}")
            else:
                st.success(_msg + " 배송완료·구매확정 주문의 정산금액이 실제 값으로 바뀝니다.")
    if st.button("💰 쿠팡 실제 정산금액 가져오기 (최근 40일 매출내역)", width="stretch",
                 help="배송완료·구매확정된 주문의 '실제 정산금액'을 쿠팡 매출내역에서 가져와 표에 반영합니다. "
                      "신규·발송대기는 아직 매출인식 전이라 추정치(수수료 12% 가정)로 표시됩니다."):
        with st.spinner("쿠팡 매출내역(정산) 조회 중입니다... (건수 많으면 조금 걸립니다)"):
            _r = sync_service.sync_settlement_amounts(date.today() - timedelta(days=40), date.today())
        st.session_state["home_settlement_result"] = _r
        st.rerun()

    st.divider()

    # ------------------------------------------------------
    # 현황 건수 — 홈 조회 기간(7/14/30/60/90일)을 선택. 기본 30일. (각 탭은 탭에 설정한 기간을 씁니다)
    # ------------------------------------------------------
    _pcol, _ = st.columns([2, 5])
    with _pcol:
        home_days = st.selectbox(
            "홈 조회 기간", [7, 14, 30, 60, 90], index=2,
            format_func=lambda d: f"최근 {d}일", key="home_period_days",
        )
    _home_cutoff = (date.today() - timedelta(days=home_days)).isoformat()

    def _count_recent(items, date_field):
        """선택한 홈 기간(_home_cutoff 이후)에 접수된 건만 셉니다."""
        n = 0
        for item in items or []:
            when = (item.get(date_field) or "")[:10]
            if when and when >= _home_cutoff:
                n += 1
        return n

    def _safe(fn, default=0):
        try:
            return fn()
        except Exception:
            return default

    counts = _safe(lambda: order_repository.count_by_work_status_since(_home_cutoff),
                   {s: 0 for s in models.WORK_STATUS_LIST})
    cancel_n = _safe(lambda: _count_recent(claims_repository.list_claims("CANCEL"), "requested_at"))
    return_n = _safe(lambda: _count_recent(claims_repository.list_claims("RETURN"), "requested_at"))
    exchange_n = _safe(lambda: _count_recent(exchange_repository.list_exchanges(), "requested_at"))
    inquiry_n = _safe(lambda: _count_recent(inquiry_repository.list_product_inquiries(), "inquiry_at"))

    # ------------------------------------------------------
    # 주문·클레임 현황 (샵마인처럼 색깔 줄 카드) — 클릭하면 그 화면으로 이동
    # ------------------------------------------------------
    col_title, col_refresh = st.columns([5, 1])
    with col_title:
        st.subheader("주문 · 클레임 현황")
        st.caption(f"건수는 **최근 {home_days}일** 기준 · **새로고침은 각 탭에 설정한 기간**으로 단계별 수집 · 색깔 줄을 누르면 그 화면으로 이동합니다.")
    with col_refresh:
        st.write("")
        if st.button("↻ 새로고침", key="home_order_refresh", width="stretch"):
            with st.spinner("신규주문·발송대기를 각 탭 설정 기간으로 가져오는 중입니다..."):
                _sync_statuses([models.WORK_STATUS_NEW, models.WORK_STATUS_READY_TO_SHIP])
            st.rerun()

    col_order, col_claim, col_manage = st.columns(3)
    with col_order:
        st.markdown("📦 **주문 현황**")
        st.markdown(
            _status_pill("신규주문", counts[models.WORK_STATUS_NEW], "신규주문", "#FAECE7", "#7A2E15", "#1D9E75")
            + _status_pill("발송대기", counts[models.WORK_STATUS_READY_TO_SHIP], "발송대기", "#FAEEDA", "#7A5B0B", "#1D9E75"),
            unsafe_allow_html=True,
        )
    with col_claim:
        st.markdown("🔁 **클레임 현황**")
        st.markdown(
            _status_pill("취소요청", cancel_n, "취소주문", "#FCEBEB", "#A32D2D", "#E24B4A")
            + _status_pill("반품요청", return_n, "반품주문", "#F9DCE6", "#993556", "#D4537E")
            + _status_pill("교환요청", exchange_n, "교환주문", "#EEEDFE", "#3C3489", "#7F77DD"),
            unsafe_allow_html=True,
        )
    with col_manage:
        st.markdown("🗂 **주문관리 현황**")
        st.markdown(
            _status_pill("미수령신고", 0, "", "#FFF6C9", "#7A5B0B", "#EF9F27")
            + _status_pill("긴급/문의", inquiry_n, "긴급/문의관리", "#E1F5EE", "#0F6E56", "#1D9E75")
            + _status_pill("반품완료", 0, "반품완료(환불완료)", "#FBE3C2", "#854F0B", "#BA7517"),
            unsafe_allow_html=True,
        )

    st.divider()

    # ------------------------------------------------------
    # 배송 현황 (샵마인처럼 진한 색 바) — 클릭하면 그 화면으로 이동
    # ------------------------------------------------------
    col_title2, col_refresh2 = st.columns([5, 1])
    with col_title2:
        st.subheader("배송 현황")
    with col_refresh2:
        st.write("")
        if st.button("↻ 새로고침", key="home_delivery_refresh", width="stretch"):
            with st.spinner("배송중·배송완료를 각 탭 설정 기간으로 가져오는 중입니다..."):
                _sync_statuses([models.WORK_STATUS_SHIPPING, models.WORK_STATUS_DELIVERED])
            st.rerun()

    st.markdown(
        _delivery_bar("배송중", counts[models.WORK_STATUS_SHIPPING], "배송중", "#7BC144")
        + _delivery_bar("배송완료", counts[models.WORK_STATUS_DELIVERED], "배송완료", "#B0B01F")
        + _delivery_bar("구매확정", counts[models.WORK_STATUS_PURCHASE_CONFIRMED], "구매확정", "#2FA02F")
        + _delivery_bar("정산관리(베타)", None, "", "#5B8DEF")
        + _delivery_bar("상품평관리(베타)", None, "", "#A15BD6"),
        unsafe_allow_html=True,
    )

    st.divider()

    # ------------------------------------------------------
    # 상점별 현황 표 (우리 DB 누적 기준 - 실시간 조회는 상점별로 나눠서
    # 보여주기엔 너무 오래 걸려서, 여기는 그대로 DB 누적치를 씁니다)
    # ------------------------------------------------------
    st.subheader("상점별 현황")
    st.caption("아래 표는 우리 DB에 누적된 건수 기준입니다 (위 카드의 쿠팡 실시간 건수와는 다를 수 있습니다).")

    per_account = order_repository.count_by_work_status_per_market_account()

    if not per_account:
        st.info("등록된 상점이 없습니다. 설정 화면에서 마켓 계정을 먼저 등록해주세요.")
        return

    rows = []
    for account in per_account:
        counts = account["counts"]
        rows.append(
            {
                "쇼핑몰": _PLATFORM_DISPLAY.get(account["platform"], account["platform"]),
                "쇼핑몰ID": account["market_id_display"],
                "별칭(쇼핑몰계정)": account["market_name"],
                "신규주문": counts[models.WORK_STATUS_NEW],
                "발송대기": counts[models.WORK_STATUS_READY_TO_SHIP],
                "취소요청": "-",
                "반품요청": "-",
                "반품(환불)완료": "-",
                "교환요청": "-",
                "미수령신고": "-",
                "긴급/문의": "-",
                "배송중": counts[models.WORK_STATUS_SHIPPING],
                "배송완료": counts[models.WORK_STATUS_DELIVERED],
                "구매확정": counts[models.WORK_STATUS_PURCHASE_CONFIRMED],
            }
        )

    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "취소요청/반품요청/반품(환불)완료/교환요청/미수령신고/긴급·문의는 아직 관리 기능이 없어 '-'로 표시됩니다."
    )
