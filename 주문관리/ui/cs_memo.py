# ==========================================================
# CS메모관리 화면 (ui/cs_memo.py)
# ----------------------------------------------------------
# 각 주문 상세내역의 'CS메모(고객 특이사항)' 칸에 적어둔 메모들을 한곳에 모아
# 목록으로 보여줍니다. (샵마인 CS메모관리 화면 구성 참고)
# 행을 클릭하면 오른쪽 상세에서 메모를 보고 바로 수정할 수 있습니다.
# ==========================================================

from datetime import datetime

import pandas as pd
import streamlit as st

from repositories import order_repository
from ui import common

# 목록에 보여줄 컬럼(메모 중심). build_full_row가 만들어주는 키 + 우리가 추가하는 '메모작성일'.
_DISPLAY_COLUMNS = [
    "No", "메모작성일", "쇼핑몰", "쇼핑몰ID", "주문일(약식)", "주문번호", "CS메모", "구매자", "수령자", "상품명",
]
_RENAME = {"주문일(약식)": "주문일", "CS메모": "메모"}


def _memo_datetime(order: dict) -> str:
    """메모 저장 시각 문자열. 예전 메모(저장시각 없음)는 주문 최근수정시각으로 대체합니다."""
    return (order.get("cs_memo_updated_at") or order.get("last_updated_at") or "")


def _memo_date(order: dict):
    """메모 저장 '날짜'(date). 기간 필터 비교용. 못 읽으면 None(→ 필터에서 항상 포함)."""
    s = _memo_datetime(order)
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def render() -> None:
    st.header("CS메모관리")
    st.caption(
        "상세내역에서 적어둔 CS메모(고객 특이사항)가 있는 주문 목록입니다. 메모를 저장하면 "
        "그 시각이 '메모작성일'로 기록되고, 아래 기간(메모작성일)으로 걸러볼 수 있습니다. "
        "표에서 체크하면 오른쪽에서 메모를 보고 수정·완료처리할 수 있습니다."
    )
    # 새로고침: 다른 화면에서 메모를 추가/수정·완료처리한 걸 이 목록에 즉시 반영합니다.
    if st.button("🔄 새로고침", key="cs_memo_refresh", help="메모 추가·수정·완료 처리 내용을 목록에 다시 불러옵니다."):
        st.rerun()

    orders = order_repository.list_orders_with_cs_memo()
    if not orders:
        st.info(
            "아직 저장된 CS메모가 없습니다. 각 주문의 상세내역에서 'CS메모' 칸에 적고 저장하면 "
            "여기 한곳에 모여서 보입니다."
        )
        return

    # 기간(메모작성일) 필터. CS메모는 오래 쌓이므로 기본 범위를 넓게(1년) 잡습니다.
    period_from, period_to = common.render_period_picker(
        "cs_memo", "메모작성일(시작)", "메모작성일(종료)", default_days=365
    )

    rows = [common.build_full_row(order, idx + 1, reveal=True) for idx, order in enumerate(orders)]

    keyword = st.text_input(
        "CS메모 검색", placeholder="메모 내용, 주문번호, 수령자, 상품명 등으로 검색", key="cs_memo_search"
    )

    pairs = []
    for order, row in zip(orders, rows):
        memo_date = _memo_date(order)
        if memo_date is not None and not (period_from <= memo_date <= period_to):
            continue
        if not common.matches_search(row, keyword):
            continue
        # 표에 '메모작성일'(분 단위까지) 컬럼을 추가합니다. (ISO의 'T'는 공백으로 보기 좋게)
        row["메모작성일"] = _memo_datetime(order)[:16].replace("T", " ")
        pairs.append((order, row))

    if not pairs:
        st.info("조건에 맞는 CS메모가 없습니다.")
        return
    filtered_orders = [pair[0] for pair in pairs]
    filtered_rows = [pair[1] for pair in pairs]

    st.caption(f"총 {len(filtered_rows)}건")

    display_df = pd.DataFrame(filtered_rows)[_DISPLAY_COLUMNS].rename(columns=_RENAME)

    # 다른 단계와 같은 AG-Grid 표(왼쪽 체크박스+머리글 전체선택, 컬럼 드래그 순서저장).
    from ui import aggrid_table

    st.caption(
        "표 왼쪽 체크박스를 체크하면 오른쪽 옆에서 메모를 보고 수정할 수 있습니다. "
        "컬럼(항목)은 마우스로 끌어 순서를 바꿀 수 있고 자동 저장됩니다."
    )
    def _cs_memo_detail(order):
        # 처리완료 토글 버튼 — 완료로 바꾸면 목록에서 그 줄이 초록색으로 표시됩니다.
        _done = bool(order.get("cs_memo_done"))
        if _done:
            st.success("✅ 처리완료된 CS메모입니다.")
            if st.button("↩ 완료 취소(다시 처리 필요로)", key=f"csdone_undo_{order['id']}", width="stretch"):
                order_repository.set_cs_memo_done(order["id"], False)
                st.rerun(scope="app")
        else:
            if st.button("✅ 이 CS메모 처리완료", type="primary", key=f"csdone_{order['id']}", width="stretch"):
                order_repository.set_cs_memo_done(order["id"], True)
                st.rerun(scope="app")
        st.divider()
        common.render_full_detail(order, reveal=True)

    # 상세 없을 땐 표 전체폭, 체크하면 표+상세. 체크는 fragment로 처리해 스크롤이 안 튐.
    common.render_grid_detail(
        display_df, key="cs_memo", orders=filtered_orders,
        detail_renderer=_cs_memo_detail,
        empty_caption="표 왼쪽 체크박스를 체크하면 여기서 메모를 보고 수정·완료처리할 수 있습니다.",
        ratios=(2, 1), height=420,
    )
