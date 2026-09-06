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

import hashlib
import json

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, DataReturnMode, GridOptionsBuilder, JsCode

from repositories import settings_repository

# 주문 리스트로 되돌리기 위한 숨김 인덱스 컬럼
IDX_COL = "_idx"
# 표 안 버튼 클릭을 파이썬으로 전달할 숨김 컬럼
BTN_CLICK_COL = "__btn_click"
# CS메모가 있는 주문을 줄 전체 붉게 표시(샵마인처럼)하기 위한 숨김 플래그 컬럼
CS_FLAG_COL = "__has_cs"
# 주문수량이 2개 이상인 주문을 줄 전체 파랗게 표시하기 위한 숨김 플래그 컬럼
QTY_FLAG_COL = "__qty2"

# 특정 컬럼을 '줄마다 버튼'으로 렌더합니다. 값이 'GR'로 시작하면(접수됨) 초록 글씨,
# 아니면 '퀵스타 연동' 버튼. 버튼 클릭 시 그 행의 _idx를 숨김 컬럼(__btn_click)에 넣어
# cellValueChanged 이벤트로 파이썬에 전달합니다.
_BUTTON_RENDERER = JsCode(
    """
class BtnCell {
  init(params) {
    const v = (params.value == null ? '' : params.value).toString();
    this.eGui = document.createElement('span');
    if (v && v.indexOf('GR') === 0) {
      this.eGui.innerText = '접수됨 ' + v;
      this.eGui.style.cssText = 'color:#2e7d32;font-size:12px;';
    } else {
      const btn = document.createElement('button');
      btn.innerText = '퀵스타 연동';
      btn.style.cssText = 'cursor:pointer;font-size:12px;padding:1px 8px;border:1px solid #7c4dff;border-radius:4px;background:#f3edff;color:#5e35b1;';
      this._h = function () { params.node.setDataValue('__btn_click', String(params.data['_idx']) + ':' + Date.now()); };
      btn.addEventListener('click', this._h);
      this._btn = btn;
      this.eGui.appendChild(btn);
    }
  }
  getGui() { return this.eGui; }
  refresh() { return false; }
  destroy() { if (this._btn && this._h) this._btn.removeEventListener('click', this._h); }
}
"""
)

# 파이썬으로 결과를 돌려보낼 AG-Grid 이벤트.
# 컬럼 이동/크기변경은 '드래그가 끝난 뒤'에만 반영되게 디바운스(1.2초)를 걸어,
# 드래그하는 동안 계속 새로고침되며 화면이 흔들리는 걸 막습니다.
_UPDATE_ON = [
    "selectionChanged",
    "cellValueChanged",
    "sortChanged",  # 머리글 클릭 정렬을 파이썬으로 잡아 pandas 정렬로 바꿔 저장(안 풀리게)
    ("columnMoved", 1200),
    ("columnResized", 1200),
    ("columnPinned", 1200),
]


def render_orders_grid(df: pd.DataFrame, key: str, orders: list, editable: dict = None, height: int = 520, button_col: str = None):
    """
    df: 표시용 DataFrame. 함수가 맨 앞에 '_idx'(orders 인덱스) 컬럼을 넣습니다.
    orders: 주문 리스트(선택/상세 매핑용, df 행과 같은 순서).
    editable: {컬럼명: {"cellEditor":.., "cellEditorParams":..}} 표 안에서 편집 가능한 컬럼.
    반환: (selected_orders, edited_df)
    """
    grid_df = df.copy()
    grid_df.insert(0, IDX_COL, list(range(len(orders))))

    # ★컬럼 순서 복원의 핵심: AG-Grid의 columns_state는 너비·숨김은 복원해도 '순서'는
    # 복원하지 않습니다. 그래서 저장된 상태에서 순서(colId 배열)를 뽑아 DataFrame 컬럼
    # 자체를 재정렬합니다. → AG-Grid 초기 컬럼 순서 = 저장 순서 → 새로고침해도 유지.
    col_setting_key = f"aggrid_colstate:{key}"
    saved = settings_repository.get_setting(col_setting_key)
    prev_state = None
    if saved:
        try:
            prev_state = json.loads(saved)
        except Exception:
            prev_state = None
    prev_order = [c.get("colId") for c in (prev_state or []) if c.get("colId")]
    if prev_order:
        ordered = [c for c in prev_order if c in grid_df.columns]
        if ordered:
            rest = [c for c in grid_df.columns if c not in ordered]
            grid_df = grid_df[ordered + rest]

    # 줄마다 버튼을 넣을 컬럼(예: 퀵스타)이 있으면, 클릭 전달용 숨김 컬럼을 추가합니다.
    has_button = bool(button_col) and button_col in grid_df.columns
    if has_button:
        grid_df[BTN_CLICK_COL] = ""

    # CS메모가 있는 주문 → 줄 전체를 붉게(샵마인처럼). 숨김 플래그 컬럼으로 판단.
    # (표시 컬럼에서 CS메모를 빼도 항상 붉게 표시되도록 별도 숨김 컬럼을 씁니다)
    if len(orders) == len(grid_df):
        grid_df[CS_FLAG_COL] = [1 if str((o or {}).get("cs_memo") or "").strip() else 0 for o in orders]

    # 주문수량 2개 이상 → 줄 전체를 파랗게. '수량' 컬럼이 있으면 그 값, 없으면 주문 items 합으로 판단.
    def _qty2_flag(o, row):
        v = row.get("수량") if isinstance(row, dict) else None
        try:
            if v is not None and str(v).strip() not in ("", "-", "nan"):
                return 1 if int(float(str(v).strip())) >= 2 else 0
        except Exception:
            pass
        try:
            return 1 if sum((it.get("quantity") or 0) for it in ((o or {}).get("items") or [])) >= 2 else 0
        except Exception:
            return 0
    if len(orders) == len(grid_df):
        _rows_for_qty = df.to_dict("records") if len(df) == len(orders) else [{}] * len(orders)
        grid_df[QTY_FLAG_COL] = [_qty2_flag(o, r) for o, r in zip(orders, _rows_for_qty)]

    # ★정렬은 'AG-Grid native'가 아니라 '파이썬(pandas)으로 데이터 자체를 정렬'합니다.
    #   native 정렬은 새로고침(퀵스타/수집 등)마다 지워져 자꾸 풀렸습니다. 대신 저장된 정렬
    #   (aggrid_sort:{key} = {colId, sort})대로 grid_df를 매 렌더 직접 정렬해서 넘깁니다.
    #   → 어떤 새로고침·퀵스타에도 데이터가 이미 그 순서라 절대 안 풀립니다. _idx로 선택/상세 매핑.
    _sort_setting_key = f"aggrid_sort:{key}"
    _saved_sort = None
    try:
        _ss = settings_repository.get_setting(_sort_setting_key)
        if _ss:
            _saved_sort = json.loads(_ss)
    except Exception:
        _saved_sort = None
    if _saved_sort and _saved_sort.get("colId") in grid_df.columns:
        try:
            grid_df = grid_df.sort_values(
                by=_saved_sort["colId"],
                ascending=(_saved_sort.get("sort") != "desc"),
                kind="stable", key=lambda s: s.astype(str),
            )
        except Exception:
            pass

    # 위젯 키: '컬럼 순서 지문 + 상세닫기 nonce + 정렬 지문'. 정렬이 바뀌면 key도 바뀌어 그리드를
    #   깨끗이 새로(remount) 그림 → native 정렬 잔상이 안 남아 다음 클릭이 정확히 잡힘.
    order_sig = "|".join(str(c) for c in grid_df.columns)
    order_hash = hashlib.md5(order_sig.encode("utf-8")).hexdigest()[:8]
    reset_nonce = st.session_state.get(f"aggrid_reset_nonce:{key}", 0)
    _sort_sig = json.dumps(_saved_sort, ensure_ascii=False) if _saved_sort else "none"
    _sort_hash = hashlib.md5(_sort_sig.encode("utf-8")).hexdigest()[:6]
    # ★표시되는 '행(주문) 목록' 지문. 검색(필터)으로 행이 바뀌면 key도 바뀌어 그리드를
    #   새로 그려(remount) 새 결과를 보여줍니다. (AG-Grid는 key가 그대로면 데이터를 갱신하지
    #   않아, 검색을 바꿔도 표가 안 바뀌던 문제 해결) 행 '내용(셀 편집)'이 아니라 '주문 식별자'로
    #   만들어, 같은 행을 편집할 때는 remount되지 않아 편집값이 유지됩니다.
    try:
        _rows_sig = "|".join(
            str((o or {}).get("id") or (o or {}).get("market_order_id") or i)
            for i, o in enumerate(orders)
        )
    except Exception:
        _rows_sig = str(len(orders))
    _rows_hash = hashlib.md5(_rows_sig.encode("utf-8")).hexdigest()[:8]
    widget_key = f"aggrid_{key}_{order_hash}_{reset_nonce}_{_sort_hash}_{_rows_hash}"
    st.session_state[f"aggrid_widgetkey:{key}"] = widget_key

    gb = GridOptionsBuilder.from_dataframe(grid_df)
    # 컬럼이 다 쭈그러들지 않게 최소너비를 주고(넘치면 가로 스크롤), 마우스로 옮길 수 있게 함.
    # sortable=True: 각 컬럼 머리글을 클릭하면 오름차순→내림차순→해제로 정렬됩니다.
    # (정렬을 걸면 그 상태에서는 행(순번) 드래그가 안 되는 게 정상입니다 - 정렬 해제하면 다시 됨)
    gb.configure_default_column(
        editable=False, resizable=True, sortable=True, filter=False, suppressMovable=False, minWidth=120
    )
    # 다중선택. 셀을 클릭한다고 선택되지 않게(=체크박스로만 선택) 해서, 편집할 때 선택이 안 흔들리게.
    gb.configure_selection(selection_mode="multiple", use_checkbox=False, suppressRowClickSelection=True)
    gb.configure_column(IDX_COL, hide=True)
    if CS_FLAG_COL in grid_df.columns:
        gb.configure_column(CS_FLAG_COL, hide=True)
    if QTY_FLAG_COL in grid_df.columns:
        gb.configure_column(QTY_FLAG_COL, hide=True)
    # 'No' 컬럼에 체크박스 + 머리글 전체선택 + 행 드래그 핸들(왼쪽 고정).
    if "No" in grid_df.columns:
        gb.configure_column(
            "No", pinned="left", width=120, minWidth=120,
            checkboxSelection=True, headerCheckboxSelection=True, rowDrag=True,
            sortable=False,  # 'No'는 전체선택 체크박스+행 드래그용이라 정렬에서 제외
        )
    if has_button:
        gb.configure_column(button_col, cellRenderer=_BUTTON_RENDERER, editable=False, minWidth=130)
        gb.configure_column(BTN_CLICK_COL, hide=True, editable=True)
    if editable:
        for col, cfg in editable.items():
            if col in grid_df.columns:
                gb.configure_column(col, editable=True, minWidth=140, **cfg)
    # enableCellTextSelection: 표 안 글자를 마우스로 드래그해 선택하고 Ctrl+C로 복사할 수 있게
    # 합니다(일반 웹페이지처럼). ensureDomOrder와 함께 써야 선택 순서가 화면 순서대로 됩니다.
    # 줄 전체 색: CS메모 있으면 붉게(우선), 없고 주문수량 2개 이상이면 파랗게.
    _row_style = JsCode(
        "function(params){ if (params.data) {"
        " if (params.data['__has_cs']) return { color: '#D32F2F', fontWeight: '600' };"
        " if (params.data['__qty2']) return { color: '#1565C0', fontWeight: '600' };"
        " } return null; }"
    )
    gb.configure_grid_options(
        rowDragManaged=True, animateRows=True,
        enableCellTextSelection=True, ensureDomOrder=True,
        getRowStyle=_row_style,
        # ★칸 편집 후 엔터를 안 치고 표 밖(버튼 등)을 눌러도 입력이 '확정'되게 함.
        #   (안 넣으면 편집 중인 값이 파이썬에 전달 안 돼서 '저장할 값 없음'이 됨)
        stopEditingWhenCellsLoseFocus=True,
    )

    # 지금 정렬 중인 컬럼 머리글에 방향 화살표(▲오름/▼내림)를 글자로 표시(데이터는 위에서 pandas로
    #   이미 정렬됨). native 화살표는 remount마다 사라지므로 글자로 직접 표시해 항상 보이게 함.
    if _saved_sort and _saved_sort.get("colId") in grid_df.columns:
        _sc = _saved_sort["colId"]
        _sar = " ▼" if _saved_sort.get("sort") == "desc" else " ▲"
        gb.configure_column(_sc, headerName=f"{_sc}{_sar}")

    grid_options = gb.build()

    # 표 높이를 실제 행 수에 맞춰 줄이되, 최대 760px(약 21행)까지 길게 보여줍니다.
    # 주문이 많으면 그 안에서 스크롤되고, 상세내역은 표 '오른쪽 옆'에 나오므로
    # 표가 길어도 상세가 가려지지 않습니다.
    _row_h = 34
    _fitted = 44 + max(len(orders), 1) * _row_h + 6
    height = max(140, min(height, _fitted, 760))

    response = AgGrid(
        grid_df,
        gridOptions=grid_options,
        height=height,
        update_on=_UPDATE_ON,
        data_return_mode=DataReturnMode.AS_INPUT,
        allow_unsafe_jscode=True,
        key=widget_key,
        # 순서는 위 df 재정렬로 복원 → 여기선 columns_state 재적용 안 함(재적용하면 드래그 중 흔들림).
        columns_state=None,
        theme="streamlit",
    )

    # 드래그가 끝나면(디바운스 후) 바뀐 '컬럼 순서'만 저장 → 다음 새로고침 때 그 순서로 복원.
    # (순서가 실제로 바뀐 경우에만 저장 → 너비 변화 등으로 불필요하게 저장/재렌더 안 함.)
    try:
        new_state = response.columns_state
        new_order = [c.get("colId") for c in (new_state or []) if c.get("colId")]
        if new_order and new_order != prev_order:
            settings_repository.set_setting(col_setting_key, json.dumps(new_state))
    except Exception:
        pass

    # 머리글 클릭 정렬 → pandas 정렬(aggrid_sort)로 바꿔 저장. native 정렬은 remount마다 비워지므로
    #   클릭 시 보고된 컬럼 기준으로 방향을 순환(오름↔내림). 저장이 바뀌면 정렬 지문이 바뀌어
    #   그리드가 새로(remount) 그려져 native 잔상이 안 남음 → 다음 클릭이 정확히 잡히고 안 풀림.
    _need_sort_rerun = False
    try:
        _native_col = None
        for c in (response.columns_state or []):
            if c.get("sort"):
                _native_col = c.get("colId")
                break
        if _native_col:  # 사용자가 머리글을 눌러 native 정렬이 걸림
            if _saved_sort and _saved_sort.get("colId") == _native_col:
                _newdir = "desc" if _saved_sort.get("sort") != "desc" else "asc"
            else:
                _newdir = "asc"
            _new_sort_json = json.dumps({"colId": _native_col, "sort": _newdir}, ensure_ascii=False)
            if settings_repository.get_setting(_sort_setting_key) != _new_sort_json:
                settings_repository.set_setting(_sort_setting_key, _new_sort_json)
                _need_sort_rerun = True
    except Exception:
        pass
    if _need_sort_rerun:
        st.rerun()


    # 표 안 버튼을 누른 행이 있으면 그 클릭값(예: "3:169..." = idx:타임스탬프)을 돌려줍니다.
    # ★여러 행을 눌렀다 하면 각 행에 예전 클릭값이 남아있으므로, '첫 행'이 아니라
    #   '타임스탬프가 가장 큰(=가장 최근에 누른)' 값을 골라야 합니다. (첫 행 방식이면
    #   앞 행의 옛 클릭값이 뒤 행의 새 클릭을 가려서 '됐다 안됐다' 현상이 생김)
    clicked_raw = None
    if has_button:
        try:
            best_ts = -1
            for r in response.data.to_dict("records"):
                val = r.get(BTN_CLICK_COL)
                s = "" if val is None else str(val).strip()
                if not s or s.lower() == "nan":
                    continue
                try:
                    ts = int(s.split(":", 1)[1])
                except (IndexError, ValueError):
                    ts = 0
                if ts > best_ts:
                    best_ts = ts
                    clicked_raw = s
        except Exception:
            clicked_raw = None

    return _selected_orders(response, orders), response.data, clicked_raw


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
