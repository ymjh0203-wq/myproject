# ==========================================================
# AG-Grid 기반 주문 표 (ui/aggrid_table.py)
# ----------------------------------------------------------
# Streamlit 기본 표로는 "표 안 편집 + 드래그 순서변경 저장 + 체크박스/전체선택 +
# 행 클릭 상세"를 동시에 못 해서, streamlit-aggrid(AG-Grid)로 표를 그립니다.
#
# 지원:
#   - 왼쪽 체크박스 + 머리글 체크박스(전체선택)   → 'No' 컬럼에 checkboxSelection
#   - 마우스로 컬럼 드래그 순서변경 → DB에 저장     → columns_state + COLUMN_MOVED
#   - 마우스로 행 드래그 순서변경(순번)             → rowDragManaged + rowDrag
#   - 특정 컬럼 표 안에서 직접 편집(택배사/송장)     → editable + cellEditor
# ==========================================================

import json

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, DataReturnMode, GridOptionsBuilder, GridUpdateMode

from repositories import settings_repository

# 주문 리스트로 되돌리기 위한 숨김 인덱스 컬럼
IDX_COL = "_idx"

# 선택/편집/컬럼변경 이벤트가 나면 파이썬으로 결과가 돌아오게 합니다(저장·상세 갱신).
_UPDATE_MODE = (
    GridUpdateMode.SELECTION_CHANGED
    | GridUpdateMode.VALUE_CHANGED
    | GridUpdateMode.COLUMN_MOVED
    | GridUpdateMode.COLUMN_RESIZED
    | GridUpdateMode.COLUMN_PINNED
    | GridUpdateMode.COLUMN_VISIBLE
)


def render_orders_grid(df: pd.DataFrame, key: str, orders: list, editable: dict = None, height: int = 520):
    """
    df: 표시용 DataFrame. 함수가 맨 앞에 '_idx'(orders 인덱스) 컬럼을 넣습니다.
    orders: 주문 리스트(선택/상세 매핑용, df 행과 같은 순서).
    editable: {컬럼명: {"cellEditor":.., "cellEditorParams":..}} 표 안에서 편집 가능한 컬럼.
    반환: (selected_orders, edited_df)
    """
    grid_df = df.copy()
    grid_df.insert(0, IDX_COL, list(range(len(orders))))

    gb = GridOptionsBuilder.from_dataframe(grid_df)
    # 컬럼이 다 쭈그러들지 않게 최소너비를 주고(넘치면 가로 스크롤), 마우스로 옮길 수 있게 함.
    gb.configure_default_column(
        editable=False, resizable=True, sortable=False, filter=False, suppressMovable=False, minWidth=120
    )
    # 다중선택. 셀을 클릭한다고 선택되지 않게(=체크박스로만 선택) 해서, 편집할 때 선택이 안 흔들리게.
    gb.configure_selection(selection_mode="multiple", use_checkbox=False, suppressRowClickSelection=True)
    gb.configure_column(IDX_COL, hide=True)
    # 첫 '보이는' 컬럼(No)에 체크박스 + 머리글 전체선택 + 행 드래그 핸들을 답니다.
    if "No" in grid_df.columns:
        gb.configure_column(
            "No", pinned="left", width=120, minWidth=120,
            checkboxSelection=True, headerCheckboxSelection=True, rowDrag=True,
        )
    if editable:
        for col, cfg in editable.items():
            if col in grid_df.columns:
                gb.configure_column(col, editable=True, minWidth=140, **cfg)
    gb.configure_grid_options(rowDragManaged=True, animateRows=True)
    grid_options = gb.build()

    # 저장된 컬럼 순서/너비 복원(DB에 JSON 보관 → 재시작해도 유지)
    saved = settings_repository.get_setting(f"aggrid_colstate:{key}")
    col_state = None
    if saved:
        try:
            col_state = json.loads(saved)
        except Exception:
            col_state = None

    response = AgGrid(
        grid_df,
        gridOptions=grid_options,
        height=height,
        update_mode=_UPDATE_MODE,
        data_return_mode=DataReturnMode.AS_INPUT,
        allow_unsafe_jscode=True,
        key=f"aggrid_{key}",
        columns_state=col_state,
        theme="streamlit",
    )

    # 드래그로 바뀐 컬럼 순서/너비를 자동 저장합니다.
    try:
        new_state = response.columns_state
        if new_state:
            settings_repository.set_setting(f"aggrid_colstate:{key}", json.dumps(new_state))
    except Exception:
        pass

    return _selected_orders(response, orders), response.data


def _selected_orders(response, orders: list) -> list:
    """AgGrid 응답에서 선택된 행 → 주문 리스트로 되돌립니다(버전별 형식 대응)."""
    sel = getattr(response, "selected_rows", None)
    if sel is None:
        return []
    try:
        if isinstance(sel, pd.DataFrame):
            if sel.empty or IDX_COL not in sel.columns:
                return []
            idxs = sel[IDX_COL].tolist()
        else:
            idxs = [row.get(IDX_COL) for row in sel]
        result = []
        for i in idxs:
            if i is None:
                continue
            i = int(i)
            if 0 <= i < len(orders):
                result.append(orders[i])
        return result
    except Exception:
        return []
