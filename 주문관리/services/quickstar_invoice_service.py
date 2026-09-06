# ==========================================================
# 퀵스타 배대지 운송장(송장번호) 자동 조회·매칭 (services/quickstar_invoice_service.py)
# ----------------------------------------------------------
# 발송대기 주문의 퀵스타 GR신청번호(quickstar_order_no)로 배대지 조회 API
# (quickstar_client.fetch_application)를 호출해서, 배대지에서 발급된 국내 택배
# 운송장번호(+택배사)를 가져와 주문에 매칭합니다.
#
#   - 송장번호 = data.invoice (배대지 접수 시점에 바로 발급되는 CJ 가송장번호)
#                ★실측 확인: 국내출고(outday) 전이라도 접수(inday) 즉시 채워져 있음.
#                아주 드물게 아직 안 나온 경우만 비어 있음 → 그 주문은 '가송장 아직 없음'으로 건너뜀
#   - 택배사   = data.invoiceNo(퀵스타 내부 택배사코드) → 쿠팡 택배사코드로 매핑
#                실측 데이터상 퀵스타 국내배송은 전부 '28' = CJ대한통운(CJGLS)
#   - 대형화물(별도 송장)은 data.largeInvoice 에 들어옴 → 송장은 채우되 택배사는
#     기본(CJ)로 두고 사용자가 표에서 확인/수정하게 함
#
# ★ 이 조회는 Playwright 자동화 브라우저가 아니라 HTTP API(인증키)로 동작하므로
#   퀵스타 로그인/데몬 없이 언제든 실행됩니다.
# ★ 여기서는 '조회 + 표에 채우기'까지만 합니다. 실제 쿠팡 송장 등록(발송처리)은
#   판매자가 표를 확인한 뒤 '발송처리' 버튼으로 실행합니다(되돌리기 어려운 작업이라).
# ==========================================================

import re

from integrations import quickstar_client
from repositories import order_repository


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))

# 퀵스타 invoiceNo(내부 택배사 코드) → 쿠팡 택배사코드(delivery_company_code)
# 실측(등록완료 주문 40건 교차확인): 27·28 모두 CJ대한통운(CJGLS). 쿠팡 등록명 "CJ 대한통운".
QS_COURIER_TO_COUPANG = {
    "27": "CJGLS",  # CJ대한통운
    "28": "CJGLS",  # CJ대한통운 (퀵스타 국내배송 기본)
}
# 매핑에 없는 값이 오면 이 기본값을 씁니다(실측상 퀵스타 국내배송은 전부 CJ대한통운).
# 혹시 다른 택배사가 나오면 판매자가 표의 '택배사' 드롭다운에서 바꾼 뒤 발송처리하면 됩니다.
DEFAULT_COUPANG_COURIER = "CJGLS"


def _extract_tracking(data: dict):
    """배대지 조회 응답의 data에서 (쿠팡택배사코드, 송장번호)을 뽑습니다.
    아직 송장이 발급되지 않았으면 (None, None)."""
    invoice = str(data.get("invoice") or "").strip()
    inv_no = str(data.get("invoiceNo") or "").strip()
    if invoice:
        code = QS_COURIER_TO_COUPANG.get(inv_no, DEFAULT_COUPANG_COURIER)
        return code, invoice
    # 대형화물은 별도 송장(largeInvoice)에 들어올 수 있음
    large = str(data.get("largeInvoice") or "").strip()
    if large:
        return DEFAULT_COUPANG_COURIER, large
    return None, None


def _progress(progress, i, orders):
    if progress:
        try:
            progress(i + 1, len(orders))
        except Exception:
            pass


def fetch_tracking_for_orders(orders: list, progress=None) -> dict:
    """orders(각 dict에 order_id, market_order_id, gr 포함)를 돌며 배대지 운송장을 조회합니다.

    반환:
      matched: {market_order_id(str): (택배사코드, 송장번호)}  # 표에 채울 대상
      scanned/with_gr/no_gr/filled/not_ready/errors/error_samples: 통계
    """
    matched = {}
    scanned = with_gr = filled = no_gr = not_ready = errors = 0
    error_samples = []

    for i, o in enumerate(orders):
        scanned += 1
        gr = str(o.get("gr") or "").strip()
        if not gr:
            no_gr += 1
            _progress(progress, i, orders)
            continue

        with_gr += 1
        try:
            resp = quickstar_client.fetch_application(gr)
            data = (resp or {}).get("data") or {}
            code, inv = _extract_tracking(data)
        except Exception as e:  # noqa: BLE001
            errors += 1
            if len(error_samples) < 6:
                error_samples.append(f"{o.get('market_order_id') or gr}: {e}")
            _progress(progress, i, orders)
            continue

        if inv:
            matched[str(o.get("market_order_id"))] = (code, inv)
            filled += 1
            # DB에도 기억(다음에 다시 조회 안 해도 되도록). 실패해도 표 채우기는 계속.
            try:
                order_repository.set_quickstar_invoice(o["order_id"], inv)
            except Exception:
                pass
        else:
            # 조회는 됐지만 아직 송장 발급 전(배대지가 국내로 출고하기 전)
            not_ready += 1

        _progress(progress, i, orders)

    return {
        "ok": True,
        "matched": matched,
        "scanned": scanned,
        "with_gr": with_gr,
        "no_gr": no_gr,
        "filled": filled,
        "not_ready": not_ready,
        "errors": errors,
        "error_samples": error_samples,
    }


def scan_invoice_changes(orders: list, progress=None) -> dict:
    """배송중 주문들의 GR로 배대지의 '현재 송장'을 조회해, 쿠팡에 등록된 송장과 다른 것을 찾습니다.
    (배대지가 송장을 바꿨는데 쿠팡엔 옛 송장이 남아있는 주문 = 변경 대상)
    반환: {ok, changes:[{order_id, market_order_id, receiver, courier_code, old, new}],
           scanned, with_gr, no_gr, no_invoice, errors, error_samples}"""
    changes = []
    scanned = with_gr = no_gr = no_invoice = errors = skipped_direct = 0
    error_samples = []
    for i, o in enumerate(orders):
        scanned += 1
        # ★업체직송(DIRECT)은 실제 택배 운송장이 없습니다(송장칸에 발송일 등이 들어감).
        #   배대지 CJ송장으로 바꾸면 안 되므로 변경 대상에서 제외합니다.
        if str(o.get("delivery_company_code") or "").upper() == "DIRECT":
            skipped_direct += 1
            _progress(progress, i, orders)
            continue
        gr = str(o.get("quickstar_order_no") or "").strip()
        if not gr:
            no_gr += 1
            _progress(progress, i, orders)
            continue
        with_gr += 1
        try:
            data = (quickstar_client.fetch_application(gr) or {}).get("data") or {}
            code, new_inv = _extract_tracking(data)
        except Exception as e:  # noqa: BLE001
            errors += 1
            if len(error_samples) < 6:
                error_samples.append(f"{o.get('market_order_id') or gr}: {e}")
            _progress(progress, i, orders)
            continue
        if not new_inv:
            no_invoice += 1
            _progress(progress, i, orders)
            continue
        cur_inv = str(o.get("invoice_number") or o.get("market_invoice_number") or "").strip()
        if _digits(new_inv) != _digits(cur_inv):
            changes.append({
                "order_id": o["id"],
                "market_order_id": o["market_order_id"],
                "receiver": ((o.get("shipping") or {}).get("receiver_name")) or o.get("receiver_name") or "",
                "courier_code": code or DEFAULT_COUPANG_COURIER,
                "old": cur_inv,
                "new": new_inv,
            })
        _progress(progress, i, orders)
    return {
        "ok": True,
        "changes": changes,
        "scanned": scanned,
        "with_gr": with_gr,
        "no_gr": no_gr,
        "no_invoice": no_invoice,
        "errors": errors,
        "skipped_direct": skipped_direct,
        "error_samples": error_samples,
    }
