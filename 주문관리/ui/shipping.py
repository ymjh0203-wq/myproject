# ==========================================================
# 배송중 화면 (ui/shipping.py)
# ----------------------------------------------------------
# 발송대기에서 송장을 등록하면 여기로 이동합니다. "수집하기"를 누르면
# 쿠팡에서 배송지시(DEPARTURE)/배송중(DELIVERING) 상태인 주문을 직접
# 가져올 수도 있습니다 (쿠팡 Wing에서 직접 처리한 주문도 반영됨).
#
# 목록 표(엑셀 양식 전체 컬럼 + 너비/높이 조절 슬라이더 + 클릭 시 상세정보)는
# ui/common.py의 render_full_table/render_full_detail을 공용으로 씁니다.
#
# TODO: 배송완료(FINAL_DELIVERY)로의 전환은 택배사 배송완료 스캔에 따라
# 쿠팡이 자동으로 처리하는 것으로 보입니다. 판매자가 직접 호출하는 "배송완료
# 처리" API를 아직 확인하지 못해서, 수동으로 배송완료로 넘기는 버튼은 만들지
# 않았습니다 (확인 후 추가 예정).
# ==========================================================

import streamlit as st

import models
from repositories import order_repository
from services import sync_service
from ui import common


def render() -> None:
    st.header("배송중")

    period_from, period_to = common.render_period_picker("shipping", "결제일시(시작)", "결제일시(종료)")
    collect_clicked = st.button("수집하기", type="primary", width="stretch", key="shipping_collect")

    if collect_clicked:
        if period_from > period_to:
            st.error("시작일이 종료일보다 늦을 수 없습니다.")
        else:
            # 배송중만이 아니라 전 단계를 함께 최신화합니다.
            result = common.collect_all_stages_with_progress(period_from, period_to)
            if result["status"] == "fail":
                st.error(f"주문 수집 실패: {result['error_message']}")
            else:
                closed = result.get("closed_count") or 0
                message = (
                    f"수집 완료 - 전체 단계 최신화 "
                    f"(신규 {result['new_count']}건, 갱신 {result['updated_count']}건"
                    + (f", 취소·반품 정리 {closed}건" if closed else "")
                    + ")"
                )
                if result["error_message"]:
                    st.warning(message + f"\n일부 오류: {result['error_message']}")
                else:
                    st.success(message)
                st.rerun()

    common.render_live_count_banner(models.WORK_STATUS_SHIPPING)

    st.divider()

    orders = order_repository.list_orders_by_work_status(models.WORK_STATUS_SHIPPING)

    if not orders:
        st.info("배송중 주문이 없습니다. 위 '수집하기'를 누르거나, 발송대기 화면에서 송장을 등록해주세요.")
        return

    keyword = st.text_input("배송중 목록 검색", placeholder="주문번호, 수령자, 송장번호 등으로 검색")

    # 전화번호·개인통관고유부호는 실제 업무에 매번 필요해서 항상 그대로 보여줍니다.
    reveal = True

    all_rows = [common.build_full_row(order, idx + 1, reveal=reveal) for idx, order in enumerate(orders)]

    excel_rows = [common.build_full_row(order, idx + 1, reveal=True) for idx, order in enumerate(orders)]
    excel_bytes = common.build_excel_bytes(excel_rows)
    st.download_button(
        "엑셀파일생성",
        data=excel_bytes,
        file_name="배송중.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="shipping_excel_download",
    )

    filtered_pairs = [(order, row) for order, row in zip(orders, all_rows) if common.matches_search(row, keyword)]

    if not filtered_pairs:
        st.info("검색 결과가 없습니다.")
        return

    filtered_orders = [pair[0] for pair in filtered_pairs]
    filtered_rows = [pair[1] for pair in filtered_pairs]

    # 행(셀) 클릭 → 오른쪽 상세. 표+상세를 fragment로 그려 스크롤이 위로 안 튐.
    common.render_full_table(
        filtered_rows, filtered_orders, key="shipping",
        detail_renderer=lambda o: common.render_full_detail(o, reveal),
        multi_select=True,
    )
