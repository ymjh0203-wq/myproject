# ==========================================================
# 매출 엑셀 첨부 → 원가·배송비 자동 채우기 (services/ledger_upload_service.py)
# ----------------------------------------------------------
# 사장님이 쓰는 '매출' 엑셀을 그대로 올리면, 각 행의 '마켓 주문번호'로 앱 주문을 찾아
# 그 주문의 퀵스타 GR신청번호로 배대지를 조회해서 두 값만 채웁니다.
#   - '단순매입가' 칸 ← 배대지 단가(CNY, 개당)
#   - '배송비용'   칸 ← 배대지 배송비(원, 출고완료 건만)
# 나머지 계산(매입가·구매비용·순매출·마진율 등)은 시트에 있는 수식이 자동 계산합니다.
# 못 채우거나 의심되는 행은 맨 끝 '비고(자동확인)' 칸에 사유를 적습니다.
#
# ★컬럼 위치는 '헤더 이름'으로 찾습니다(월마다 파일이 같은 양식이면 그대로 동작).
#   값(데이터)은 안 건드리고 두 칸만 채우므로, 긴 주문번호도 안전합니다.
# ==========================================================

import copy
import io

from integrations import quickstar_client
from repositories import order_repository


# 헤더 이름 후보(엑셀마다 약간 달라도 잡히게 여러 개 허용)
_H_ORDER = ["마켓 주문번호", "마켓주문번호", "주문번호"]
_H_QTY = ["주문수량", "수량"]
_H_COST = ["단순매입가"]
_H_SHIP = ["배송비용"]
_H_DATE = ["주문시간", "주문일시", "주문일", "결제 일시", "결제일시"]  # 기간 필터 기준 날짜
# ★GR(그룹번호=퀵스타 신청번호) 열이 시트에 있으면 그걸 바로 씁니다(DB 조회 불필요).
_H_GR = ["퀵스타 신청번호", "그룹번호", "GR신청번호", "GR 신청번호", "GR", "신청번호", "신청번호*"]
_NOTE_HEADER = "비고(자동확인)"


def _row_date(cell_value):
    """셀 값(문자열/날짜)에서 날짜(date)만 뽑습니다. 못 읽으면 None."""
    from datetime import date
    if cell_value is None:
        return None
    if hasattr(cell_value, "date") and not isinstance(cell_value, str):
        try:
            return cell_value.date()
        except Exception:  # noqa: BLE001
            pass
    s = str(cell_value).strip().replace("T", " ")[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _to_float(value):
    try:
        f = float(value)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _extract_costs(data: dict, qty: int):
    """배대지 응답에서 (단가CNY, 배송비원, 배대지총수량, 상품종류수)를 뽑습니다."""
    apps = data.get("appList") or []
    items = [it for a in apps for it in (a.get("appitemList") or [])]
    # ★단순매입가 = 배대지 '단가(itemMoney)' 그대로. 수량은 곱하지도 나누지도 않습니다.
    #   (상품이 여러 종이면 각 단가를 합산. 시트가 '단가 × 주문수량'을 따로 계산함)
    unit_sum = 0.0
    total_cnt = 0
    kinds = 0
    have = False
    for it in items:
        money = _to_float(it.get("itemMoney"))
        cnt = _to_float(it.get("itemCount")) or 0
        if money is not None:
            have = True
            kinds += 1
            total_cnt += cnt
            unit_sum += money
    unit = round(unit_sum, 2) if have else None
    ship = None
    wl = data.get("weightList") or [{}]
    if wl:
        ship = _to_float(wl[0].get("totalMoney"))
        if ship is None:
            pl = data.get("paymentList") or [{}]
            if pl:
                ship = _to_float(pl[0].get("payMoney"))
    ship = int(round(ship)) if ship is not None else None
    return unit, ship, int(total_cnt), kinds


def _find_headers(ws):
    """1행에서 필요한 컬럼 위치를 이름으로 찾습니다. (col_order, col_qty, col_cost, col_ship, col_note)"""
    header = {}
    last_col = 0
    for c in range(1, ws.max_column + 1):
        v = ws.cell(1, c).value
        if v is not None and str(v).strip():
            header[str(v).strip()] = c
            last_col = c

    def _pick(cands):
        for name in cands:
            if name in header:
                return header[name]
        return None

    col_order = _pick(_H_ORDER)
    col_qty = _pick(_H_QTY)
    col_cost = _pick(_H_COST)
    col_ship = _pick(_H_SHIP)
    col_date = _pick(_H_DATE)
    col_gr = _pick(_H_GR)
    col_note = header.get(_NOTE_HEADER) or (last_col + 1)  # 없으면 맨 끝에 새로 만듦
    return col_order, col_qty, col_cost, col_ship, col_date, col_gr, col_note


def _pick_sheet(wb):
    """'매출' 시트를 우선, 없으면 '마켓 주문번호'+'단순매입가'가 있는 첫 시트를 고릅니다."""
    if "매출" in wb.sheetnames:
        return wb["매출"]
    for name in wb.sheetnames:
        ws = wb[name]
        headers = {str(ws.cell(1, c).value).strip() for c in range(1, ws.max_column + 1)
                   if ws.cell(1, c).value is not None}
        if (headers & set(_H_ORDER)) and (headers & set(_H_COST)):
            return ws
    return wb[wb.sheetnames[0]]


def fill_uploaded_ledger(file_bytes: bytes, date_from=None, date_to=None, progress=None) -> dict:
    """
    업로드한 매출 엑셀에 배대지 단가·배송비를 채워 새 엑셀 bytes를 돌려줍니다.
    date_from/date_to(date, 선택): 주면 그 '주문시간' 기간 안의 행만 채우고, 기간 밖은
    건드리지 않습니다(이미 정리 끝난 앞부분 보존용). 둘 다 None이면 전체를 채웁니다.
    반환: {ok, out_bytes, sheet, date_col, stats{...,skipped}, notes:[(주문번호,사유)], error}
    """
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=False)  # 수식 보존
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"엑셀을 열 수 없습니다: {e}"}

    ws = _pick_sheet(wb)
    col_order, col_qty, col_cost, col_ship, col_date, col_gr, col_note = _find_headers(ws)
    _use_range = (date_from is not None) or (date_to is not None)
    if _use_range and not col_date:
        return {"ok": False,
                "error": f"[{ws.title}] 시트에서 기간 필터에 쓸 날짜 칸('주문시간' 등)을 못 찾았습니다. "
                         "기간 없이 전체 채우기로 해주세요."}
    missing = []
    # GR(그룹번호) 열이 있으면 주문번호 없이도 됩니다. 둘 다 없을 때만 오류.
    if not col_order and not col_gr:
        missing.append("마켓 주문번호(또는 GR/그룹번호)")
    if not col_cost:
        missing.append("단순매입가")
    if not col_ship:
        missing.append("배송비용")
    if missing:
        return {"ok": False,
                "error": f"[{ws.title}] 시트에서 '{', '.join(missing)}' 칸을 못 찾았습니다. "
                         "헤더(첫 줄) 이름이 매출 양식과 같은지 확인해주세요."}

    # 비고 헤더는 기존 헤더의 폰트를 그대로 써서 시트와 통일.
    _hdr_ref = col_order or col_gr or col_cost
    _hdr_cell = ws.cell(1, col_note)
    _hdr_cell.value = _NOTE_HEADER
    _hdr_cell.font = copy.copy(ws.cell(1, _hdr_ref).font)

    # 앱 DB: 마켓주문번호 → GR
    gr_map = order_repository.get_gr_by_market_order_ids() if hasattr(
        order_repository, "get_gr_by_market_order_ids") else None
    if gr_map is None:
        gr_map = _gr_map_fallback()

    # 채울 행 목록(주문번호 또는 GR 있는 데이터행) 먼저 수집 → 진행률 표시용
    _key_col = col_order or col_gr
    data_rows = []
    for r in range(2, ws.max_row + 1):
        v = ws.cell(r, _key_col).value
        if v is not None and str(v).strip():
            data_rows.append(r)

    gr_cache = {}
    stats = {"rows": 0, "filled_price": 0, "filled_ship": 0,
             "no_gr": 0, "not_in_db": 0, "err": 0, "suspect": 0, "skipped": 0}
    notes = []

    for idx, r in enumerate(data_rows):
        # 기간 지정 시: 그 행의 '주문시간' 날짜가 기간 밖이거나 못 읽으면 건드리지 않고 건너뜀.
        if _use_range:
            d = _row_date(ws.cell(r, col_date).value)
            if d is None or (date_from and d < date_from) or (date_to and d > date_to):
                stats["skipped"] += 1
                if progress:
                    try:
                        progress(idx + 1, len(data_rows))
                    except Exception:  # noqa: BLE001
                        pass
                continue
        stats["rows"] += 1
        moid = str(ws.cell(r, col_order).value or "").strip() if col_order else ""
        qty = int(_to_float(ws.cell(r, col_qty).value) or 0) if col_qty else 0
        # ★GR은 '시트에 적힌 값 우선'(그룹번호 열) → 없으면 주문번호로 앱 DB에서 찾음.
        sheet_gr = str(ws.cell(r, col_gr).value or "").strip() if col_gr else ""
        label = moid or sheet_gr  # 비고 목록에 보여줄 식별자
        _ref_col = col_order or col_gr or col_cost  # 폰트 복사용 기준열(항상 존재)
        note = ""
        gr = sheet_gr or (gr_map.get(moid) or "").strip()
        if not gr:
            if not sheet_gr and moid and moid not in gr_map:
                note = "앱에 없는 주문(수집 안 됨)"
                stats["not_in_db"] += 1
            else:
                note = "GR 없음(배대지 접수 안 됨)"
                stats["no_gr"] += 1
        else:
            if True:
                try:
                    if gr in gr_cache:
                        data = gr_cache[gr]
                    else:
                        data = (quickstar_client.fetch_application(gr) or {}).get("data") or {}
                        gr_cache[gr] = data
                    unit, ship, tcnt, kinds = _extract_costs(data, qty)
                    # 채운 셀 폰트를 그 행의 기존 셀과 동일하게 맞춥니다(맑은 고딕 12 등).
                    _row_font = copy.copy(ws.cell(r, _ref_col).font)
                    if unit is not None:
                        _cc = ws.cell(r, col_cost)
                        _cc.value = unit
                        _cc.font = copy.copy(_row_font)
                        _cc.number_format = "0.00"   # 단가(CNY) 소수 2자리
                        stats["filled_price"] += 1
                    if ship is not None:
                        _sc = ws.cell(r, col_ship)
                        _sc.value = ship
                        _sc.font = copy.copy(_row_font)
                        _sc.number_format = "#,##0"  # 배송비 천단위
                        stats["filled_ship"] += 1
                    reasons = []
                    if unit is None and ship is None:
                        reasons.append("배대지에 단가·배송비 없음")
                    else:
                        if unit is None:
                            reasons.append("배대지 단가 없음")
                        if ship is None:
                            reasons.append("출고 전이라 배송비 없음")
                    if kinds > 1:
                        reasons.append(f"의심:GR에 상품 {kinds}종(단가 합산)")
                        stats["suspect"] += 1
                    if tcnt and qty and tcnt != qty:
                        reasons.append(f"의심:수량불일치(배대지{tcnt}/엑셀{qty})")
                        stats["suspect"] += 1
                    note = " · ".join(reasons)
                except Exception as e:  # noqa: BLE001
                    note = f"배대지 조회 오류: {e}"
                    stats["err"] += 1
        _nc = ws.cell(r, col_note)
        _nc.value = note
        _nc.font = copy.copy(ws.cell(r, _ref_col).font)  # 비고도 그 행 폰트와 통일
        if note:
            notes.append((label, note))
        if progress:
            try:
                progress(idx + 1, len(data_rows))
            except Exception:  # noqa: BLE001
                pass

    buf = io.BytesIO()
    wb.save(buf)
    _date_col_name = ws.cell(1, col_date).value if col_date else None
    return {"ok": True, "out_bytes": buf.getvalue(), "sheet": ws.title,
            "date_col": _date_col_name, "stats": stats, "notes": notes, "error": None}


def _gr_map_fallback() -> dict:
    """order_repository에 전용 함수가 없을 때, 직접 조회해서 마켓주문번호→GR 맵을 만듭니다."""
    from database import get_connection
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT market_order_id, quickstar_order_no FROM orders"
        ).fetchall()
    finally:
        connection.close()
    gr_map = {}
    for row in rows:
        mo = str(row["market_order_id"])
        gr = row["quickstar_order_no"]
        if mo not in gr_map or (gr and not gr_map[mo]):
            gr_map[mo] = gr
    return gr_map
