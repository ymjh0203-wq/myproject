# ==========================================================
# 매출정리 원가·배송비 자동 채우기 (services/ledger_autofill_service.py)
# ----------------------------------------------------------
# 각 주문의 퀵스타 GR신청번호로 배대지 조회 API(quickstar_client.fetch_application)를
# 호출해서, 매출정리에 필요한 두 값을 자동으로 채웁니다.
#   - 원가(단가 CNY) = appList[].appitemList[].itemMoney (위안, 개당) × 수량 = 총원가
#                      → 매출정리에는 '단가' 로 넣기 위해 총원가 ÷ 주문수량
#   - 배송비(원)     = weightList[0].totalMoney (= 배대지 실제 결제총액, 출고 후에만 있음)
#
# ★ 이 조회는 Playwright 자동화 브라우저가 아니라 HTTP API(인증키)로 동작하므로
#   퀵스타 로그인/데몬 없이 언제든 실행됩니다.
# ==========================================================

from integrations import quickstar_client
from repositories import cost_repository


def _to_float(value):
    try:
        f = float(value)
        return f if f == f else None  # NaN 방지
    except (TypeError, ValueError):
        return None


def _extract_costs(data: dict, order_qty: int):
    """배대지 신청서 응답에서 (단가CNY, 배송비원)을 뽑습니다. 없으면 None."""
    apps = data.get("appList") or []
    items = [it for a in apps for it in (a.get("appitemList") or [])]
    # 단순매입가 = 배대지 '단가(itemMoney)' 그대로. 수량은 곱하지도 나누지도 않습니다.
    # (상품이 여러 종이면 각 단가 합산. 시트가 '단가 × 주문수량'을 따로 계산함)
    unit_sum = 0.0
    have_item = False
    for it in items:
        money = _to_float(it.get("itemMoney"))
        if money is not None:
            have_item = True
            unit_sum += money
    unit_cny = round(unit_sum, 2) if have_item else None

    ship = None
    wl = (data.get("weightList") or [{}])
    if wl:
        ship = _to_float(wl[0].get("totalMoney"))
        if ship is None:  # 출고 전이면 결제내역의 payMoney를 대안으로
            pl = (data.get("paymentList") or [{}])
            if pl:
                ship = _to_float(pl[0].get("payMoney"))
    ship = int(round(ship)) if ship is not None else None
    return unit_cny, ship


def autofill_costs(orders: list, progress=None) -> dict:
    """orders(각 dict에 order_id, gr, qty 포함)를 돌며 배대지 조회로 원가·배송비를 채웁니다.
    반환: {ok, scanned, with_gr, filled_price, filled_ship, no_gr, errors, error_samples}
    ※ 이미 값이 있고 이번에 못 가져온 항목은 기존 값을 지우지 않습니다."""
    order_ids = [o["order_id"] for o in orders]
    existing = cost_repository.get_map(order_ids)

    scanned = with_gr = filled_price = filled_ship = no_gr = errors = 0
    error_samples = []

    for i, o in enumerate(orders):
        scanned += 1
        gr = (o.get("gr") or "").strip()
        if not gr:
            no_gr += 1
            continue
        with_gr += 1
        try:
            resp = quickstar_client.fetch_application(gr)
            data = (resp or {}).get("data") or {}
            unit_cny, ship = _extract_costs(data, int(o.get("qty") or 0))
        except Exception as e:  # noqa: BLE001
            errors += 1
            if len(error_samples) < 6:
                error_samples.append(f"{o.get('market_order_id') or gr}: {e}")
            unit_cny, ship = None, None

        cur = existing.get(o["order_id"], {})
        new_price = unit_cny if unit_cny is not None else cur.get("unit_price_cny")
        new_ship = ship if ship is not None else cur.get("shipping_cost")
        if unit_cny is not None:
            filled_price += 1
        if ship is not None:
            filled_ship += 1

        # 새로 가져온 게 하나라도 있으면 저장(기존 값은 보존)
        if unit_cny is not None or ship is not None:
            cost_repository.upsert(o["order_id"], new_price, new_ship)

        if progress:
            try:
                progress(i + 1, len(orders))
            except Exception:
                pass

    return {
        "ok": True,
        "scanned": scanned,
        "with_gr": with_gr,
        "no_gr": no_gr,
        "filled_price": filled_price,
        "filled_ship": filled_ship,
        "errors": errors,
        "error_samples": error_samples,
    }
