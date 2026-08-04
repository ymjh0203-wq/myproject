# ==========================================================
# 교환주문 화면 (ui/exchange_orders.py)
# ----------------------------------------------------------
# 쿠팡 공식 API(exchangeRequests)로 실제 교환요청 목록을 가져옵니다.
# ("Exchange APIs" 문서, 2026-07-20 확인 - 단, 응답 필드 구조는 문서에서
# 완전히 확인 못 해서 실제 호출 결과를 보고 조정이 필요할 수 있습니다.
# integrations/coupang_client.py의 _convert_real_exchange 참고)
# ==========================================================

import streamlit as st

from repositories import exchange_repository
from services import claims_sync_service
from ui import common

SETTINGS_KEY = "exchange"


def render() -> None:
    st.header("교환주문")
    st.caption(
        "쿠팡 교환요청 API 응답의 세부 항목은 아직 실제 데이터로 완전히 검증되지 않았습니다. "
        "수집 결과가 이상하면 알려주세요 - 필드 매핑을 다시 확인하겠습니다."
    )

    period_from, period_to = common.render_period_picker("exchange", "접수일시(시작)", "접수일시(종료)")
    collect_clicked = st.button("수집하기", type="primary", width="stretch", key="exchange_collect")

    if collect_clicked:
        if period_from > period_to:
            st.error("시작일이 종료일보다 늦을 수 없습니다.")
        else:
            with st.spinner("쿠팡에서 교환요청을 가져오는 중입니다..."):
                result = claims_sync_service.sync_exchange_requests(period_from, period_to)
            if result["status"] == "fail":
                st.error(f"수집 실패: {result['error_message']}")
            else:
                st.success(
                    f"수집 완료 - 전체 {result['fetched_count']}건 "
                    f"(신규 {result['new_count']}건, 갱신 {result['updated_count']}건, "
                    f"오류 {result['error_count']}건)"
                )
                st.rerun()

    last_sync_at = claims_sync_service.get_last_sync_at(SETTINGS_KEY)
    last_count = claims_sync_service.get_last_fetched_count(SETTINGS_KEY)
    if last_sync_at:
        st.info(f"**쿠팡 기준 현재 교환요청: {last_count}건** (확인 시각: {last_sync_at})")
    else:
        st.caption("아직 한 번도 수집하지 않았습니다.")

    st.divider()

    exchanges = exchange_repository.list_exchanges()
    if not exchanges:
        st.info("교환요청 내역이 없습니다. 위 '수집하기'를 눌러보세요.")
        return

    keyword = st.text_input("교환주문 검색", placeholder="주문번호, 교환접수번호 등으로 검색")

    rows = [
        {
            "교환접수번호": e["exchange_id"],
            "주문번호": e["market_order_id"] or "-",
            "상점": e.get("market_account_name") or "-",
            "상태": e["status"] or "-",
            "접수일시": e["requested_at"] or "-",
        }
        for e in exchanges
    ]

    filtered = [row for row in rows if common.matches_search(row, keyword)]

    if not filtered:
        st.info("검색 결과가 없습니다.")
        return

    # 발송대기와 같은 구성(항목순서·정렬·너비·높이 조절 + 행 클릭 상세패널)으로 보여줍니다.
    common.render_records_table(filtered, key="exchange_table")
