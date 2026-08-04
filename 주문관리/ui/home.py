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

import models
from repositories import order_repository
from services import claims_sync_service, sync_service
from ui import common, settings

_PERIOD_DAYS = {"최근 7일": 7, "최근 14일": 14, "최근 30일": 30, "전체 기간(최근 90일로 조회)": 90}

_PLATFORM_DISPLAY = {"coupang": "쿠팡", "other": "기타"}


def _period_to_range(period_label: str):
    days = _PERIOD_DAYS.get(period_label, 30)
    return date.today() - timedelta(days=days), date.today()


def _sync_statuses(work_statuses: list, period_label: str) -> None:
    period_from, period_to = _period_to_range(period_label)
    errors = []
    for work_status in work_statuses:
        result = sync_service.sync_orders_for_stage(work_status, period_from, period_to)
        if result["status"] == "fail":
            errors.append(f"{work_status}: {result['error_message']}")
    if errors:
        st.session_state["home_sync_errors"] = errors


def _sync_everything(period_label: str) -> None:
    """
    신규주문/발송대기/배송중/배송완료 + 취소요청/반품요청/교환요청/상품문의/
    콜센터문의까지 전부 다시 수집합니다. (구매확정은 쿠팡 조회 API가 없어서
    제외합니다)

    9종류를 하나씩 순서대로 하면 너무 오래 걸려서, 어느 정도는 동시에(병렬로)
    실행합니다. 단, 한꺼번에 다 동시에 보내면 쿠팡이 "API 호출 한도 초과"로
    거부해서(2026-07-21 확인), 동시에 최대 2개까지만 실행하고, 그마저도
    호출 한도 오류가 나면 잠깐 쉬었다가 한 번 더 시도합니다.
    """
    period_from, period_to = _period_to_range(period_label)

    jobs = [
        ("신규주문", lambda: sync_service.sync_orders_for_stage(models.WORK_STATUS_NEW, period_from, period_to)),
        ("발송대기", lambda: sync_service.sync_orders_for_stage(models.WORK_STATUS_READY_TO_SHIP, period_from, period_to)),
        ("배송중", lambda: sync_service.sync_orders_for_stage(models.WORK_STATUS_SHIPPING, period_from, period_to)),
        ("배송완료", lambda: sync_service.sync_orders_for_stage(models.WORK_STATUS_DELIVERED, period_from, period_to)),
        ("취소요청", lambda: claims_sync_service.sync_claims("CANCEL", period_from, period_to)),
        ("반품요청", lambda: claims_sync_service.sync_claims("RETURN", period_from, period_to)),
        ("교환요청", lambda: claims_sync_service.sync_exchange_requests(period_from, period_to)),
        ("상품문의", lambda: claims_sync_service.sync_product_inquiries(period_from, period_to)),
        # 취소·반품 등으로 쿠팡에서 사라진 발송대기/신규 주문을 정리합니다.
        ("사라진 주문 정리", lambda: sync_service.reconcile_active_orders(period_from, period_to)),
    ]

    # 콜센터문의는 쿠팡 API가 계속 500 오류를 내서 기본으로 꺼져 있습니다.
    # (설정 > 수집 설정에서 켤 수 있습니다)
    if settings.is_call_center_sync_enabled():
        jobs.append(
            ("콜센터문의", lambda: claims_sync_service.sync_call_center_inquiries(period_from, period_to))
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
            errors.append(f"{label}: {result.get('error_message')}")

    if errors:
        st.session_state["home_sync_errors"] = errors


def _render_live_metric(work_status: str, label: str) -> None:
    count = sync_service.get_last_fetched_count(work_status)
    if count is None:
        st.metric(label, "-")
    else:
        st.metric(label, count)


def render() -> None:
    st.header("홈")

    if st.session_state.get("home_sync_errors"):
        errors = st.session_state.pop("home_sync_errors")
        st.error("일부 수집 실패:\n" + "\n".join(f"- {e}" for e in errors))

    if st.session_state.get("home_full_sync_done"):
        st.session_state.pop("home_full_sync_done")
        st.success(
            "신규주문·발송대기·배송중·배송완료·취소요청·반품요청·교환요청·상품문의·콜센터문의를 "
            "전부 다시 확인했습니다. (구매확정은 쿠팡 조회 API가 없어서 제외)"
        )

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
        "전체 한번에 새로고침 (주문 4단계 + 취소·반품·교환·문의)", type="primary", width="stretch"
    ):
        # 진행바(%+남은시간)는 _sync_everything 안에서 보여줍니다.
        _sync_everything("최근 30일")
        st.session_state["home_full_sync_done"] = True
        st.rerun()

    st.info(
        "**정산 안내**  \n"
        "표시된 정산 예상 금액은 쿠폰/포인트 등 프로모션 반영 여부에 따라 실제 정산 금액과 다를 수 있습니다.  \n"
        "정확한 금액은 쇼핑몰 판매자센터에서 확인해 주세요."
    )

    st.divider()

    # ------------------------------------------------------
    # 주문·클레임 현황 (신규주문/발송대기는 실시간 쿠팡 조회)
    # ------------------------------------------------------
    st.subheader("주문·클레임 현황")
    st.caption("아래 숫자는 우리 DB 누적 건수가 아니라, '새로고침'을 눌렀을 때 쿠팡이 실시간으로 알려준 건수입니다.")

    col_period, col_refresh = st.columns([4, 1])
    with col_period:
        order_period = st.selectbox(
            "조회 기간", list(_PERIOD_DAYS.keys()), index=2, key="home_order_period"
        )
    with col_refresh:
        st.write("")
        if st.button("새로고침", key="home_order_refresh", width="stretch"):
            with st.spinner("쿠팡에서 신규주문·발송대기 현황을 다시 가져오는 중입니다..."):
                _sync_statuses([models.WORK_STATUS_NEW, models.WORK_STATUS_READY_TO_SHIP], order_period)
            st.rerun()

    col_order, col_claim, col_manage = st.columns(3)
    with col_order:
        with st.container(border=True):
            st.markdown("**주문 현황 (쿠팡 실시간)**")
            _render_live_metric(models.WORK_STATUS_NEW, "신규주문")
            _render_live_metric(models.WORK_STATUS_READY_TO_SHIP, "발송대기")
            last_sync_at = sync_service.get_last_sync_at(models.WORK_STATUS_NEW)
            st.caption(f"확인 시각: {last_sync_at}" if last_sync_at else "아직 확인 안 함 - 새로고침을 눌러주세요.")
    with col_claim:
        with st.container(border=True):
            st.markdown("**클레임 현황**")
            st.caption("취소/반품/교환 관리 기능은 아직 없어 숫자를 표시하지 않습니다.")
            st.metric("취소요청", "-")
            st.metric("반품요청", "-")
            st.metric("교환요청", "-")
    with col_manage:
        with st.container(border=True):
            st.markdown("**주문관리 현황**")
            st.caption("미수령신고/긴급·문의/반품완료 관리 기능은 아직 없어 숫자를 표시하지 않습니다.")
            st.metric("미수령신고", "-")
            st.metric("긴급/문의", "-")
            st.metric("반품완료", "-")

    st.divider()

    # ------------------------------------------------------
    # 배송 현황 (배송중/배송완료는 실시간 쿠팡 조회, 구매확정은 DB 누적치)
    # ------------------------------------------------------
    st.subheader("배송 현황")

    col_period2, col_refresh2 = st.columns([4, 1])
    with col_period2:
        delivery_period = st.selectbox(
            "조회 기간", list(_PERIOD_DAYS.keys()), index=1, key="home_delivery_period"
        )
    with col_refresh2:
        st.write("")
        if st.button("새로고침", key="home_delivery_refresh", width="stretch"):
            with st.spinner("쿠팡에서 배송중·배송완료 현황을 다시 가져오는 중입니다..."):
                _sync_statuses([models.WORK_STATUS_SHIPPING, models.WORK_STATUS_DELIVERED], delivery_period)
            st.rerun()

    purchase_confirmed_count = order_repository.count_by_work_status()[models.WORK_STATUS_PURCHASE_CONFIRMED]

    col_delivery, col_beta = st.columns(2)
    with col_delivery:
        with st.container(border=True):
            st.markdown("**배송 (배송중·배송완료는 쿠팡 실시간)**")
            _render_live_metric(models.WORK_STATUS_SHIPPING, "배송중")
            _render_live_metric(models.WORK_STATUS_DELIVERED, "배송완료")
            st.metric("구매확정 (우리 DB 기준)", purchase_confirmed_count)
            last_sync_at2 = sync_service.get_last_sync_at(models.WORK_STATUS_SHIPPING)
            st.caption(f"확인 시각: {last_sync_at2}" if last_sync_at2 else "아직 확인 안 함 - 새로고침을 눌러주세요.")
            st.caption("구매확정은 쿠팡 판매자용 조회 API를 아직 확인 못 해 실시간 조회가 안 됩니다.")
    with col_beta:
        with st.container(border=True):
            st.markdown("**정산관리(베타) · 상품평관리(베타)**")
            st.caption("아직 만들지 않은 기능입니다. 필요하시면 다음 단계로 요청해주세요.")
            st.write("정산관리(베타) — 준비중")
            st.write("상품평관리(베타) — 준비중")

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
