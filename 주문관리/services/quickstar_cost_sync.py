# ==========================================================
# 퀵스타 신청내역 → 매입가·배송비 일괄 캐시 (services/quickstar_cost_sync.py)
# ----------------------------------------------------------
# 매출정리는 GR마다 배대지를 조회해 단가·배송비를 채우는데, 퀵스타 '신청서 조회 API'는
# 분당 10회 제한이라 다건이 아주 느립니다. 반면 퀵스타 '신청내역 목록(웹)'은 로그인
# 세션으로 AJAX 한 번에 수천 건을 줍니다(제한 없음). 그래서 웹 목록을 한 번에 받아
# GR→주문을 매칭(수취인명 대조)한 뒤, 단가·배송비를 orders의 캐시 칸에 저장합니다.
# 그러면 그 뒤 매출정리는 캐시만 보고 즉시 채웁니다(재조회 없음).
#
# ※ GR은 접수 때 우리가 그 주문에 저장한 값이라 'GR→주문'은 이미 신뢰할 수 있습니다.
#   다만 혹시 GR을 엉뚱한 주문에 넣었을 수 있어, 퀵스타 목록의 수취인명과 주문 수취인명이
#   다르면(불일치) 그 건은 캐시에 넣지 않고 건너뜁니다(나중에 API로 정상 대조하도록).
# ==========================================================

import re

import database
from integrations import quickstar_automation
from services import ledger_match


def _parse_ship_list(html: str) -> list:
    """신청내역 목록 HTML에서 [{gr, unit(CNY), ship(원), name}] 들을 뽑습니다."""
    records = []
    seen = set()
    for block in re.split(r"(?=GR\d{10,})", html or ""):
        if "GR" not in block:
            continue
        text = re.sub(r"<[^>]+>", " ", block).replace("&nbsp;", " ")
        text = re.sub(r"\s+", " ", text)
        m_gr = re.search(r"GR\d{10,}", text)
        if not m_gr:
            continue
        gr = m_gr.group(0)
        if gr in seen:
            continue
        m_unit = re.search(r"단가\s*([\d.]+)\s*CNY", text)
        m_ship = re.search(r"([\d,]+)\s*KRW", text)
        m_name = re.search(r"([가-힣A-Za-z]{2,20})\s*\(\s*(?:해운|항공)", text)
        if not (m_unit and m_ship):
            continue
        seen.add(gr)
        records.append({
            "gr": gr,
            "unit": float(m_unit.group(1)),
            "ship": int(m_ship.group(1).replace(",", "")),
            "name": (m_name.group(1) if m_name else ""),
        })
    return records


def sync_costs(sdate: str, edate: str, progress=None) -> dict:
    """퀵스타 신청내역(출고완료)을 받아 매입가·배송비를 캐시에 저장합니다.
    sdate/edate: 출고일 기준 기간(YYYY-MM-DD). 반환: 통계 dict."""
    html = quickstar_automation.fetch_ship_cost_html(sdate, edate, state="307")
    records = _parse_ship_list(html)

    stats = {"fetched": len(records), "saved": 0, "name_mismatch": 0, "no_order": 0}
    connection = database.get_connection()
    PG = database._to_pg_sql
    try:
        cur = connection.cursor()
        now = database.datetime.now().isoformat(timespec="seconds")
        for i, rec in enumerate(records):
            cur.execute(
                PG(
                    "SELECT o.id, COALESCE(s.receiver_name,'') rn, COALESCE(s.receiver_phone_raw,'') rp, "
                    "COALESCE(s.customs_phone,'') cp "
                    "FROM orders o LEFT JOIN shipping_information s ON s.order_id=o.id "
                    "WHERE o.quickstar_order_no=?"
                ),
                (rec["gr"],),
            )
            rows = cur.fetchall()
            if not rows:
                stats["no_order"] += 1
            else:
                order_name = rows[0]["rn"]
                # 수취인명 대조(둘 다 있을 때만): 다르면 캐시에 안 넣음(안전)
                if rec["name"] and order_name and \
                        ledger_match.norm_name(rec["name"]) != ledger_match.norm_name(order_name):
                    stats["name_mismatch"] += 1
                else:
                    phone = rows[0]["cp"] or rows[0]["rp"] or ""
                    # 같은 GR인 주문 행 모두에 캐시 저장(합포장 대비). 대조는 끝났으므로
                    # 매출정리의 캐시 대조가 통과하도록 주문 자체의 수취인명·전화를 저장.
                    cur.execute(
                        PG(
                            "UPDATE orders SET quickstar_cost_unit=?, quickstar_cost_ship=?, "
                            "quickstar_cost_recv_name=?, quickstar_cost_recv_phone=?, "
                            "quickstar_cost_kinds=?, quickstar_cost_tcnt=?, quickstar_cost_at=? "
                            "WHERE quickstar_order_no=?"
                        ),
                        (rec["unit"], rec["ship"], order_name or rec["name"], phone, 1, 1, now, rec["gr"]),
                    )
                    stats["saved"] += 1
            if progress and (i % 25 == 0 or i == len(records) - 1):
                try:
                    progress(i + 1, len(records))
                except Exception:  # noqa: BLE001
                    pass
        connection.commit()
    finally:
        connection.close()
    return stats
