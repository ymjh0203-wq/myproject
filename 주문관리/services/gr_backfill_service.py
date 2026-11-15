# ==========================================================
# GR신청번호 백필 (services/gr_backfill_service.py)
# ----------------------------------------------------------
# 퀵스타 접수를 '수동 제출'해서 앱이 GR신청번호(quickstar_order_no)를 대부분 못 잡은
# 기존 주문들에, 퀵스타 '신청내역'을 긁어서 GR번호를 채워(백필) 줍니다.
#
# 매칭 키:
#   1순위 = 운송장(CJ대한통운). 신청내역의 CJ운송장 = 쿠팡에 등록된 송장번호이므로 가장 확실.
#   2순위 = 수취인명. 단, 동명이인(같은 이름에 GR이 여러 개) 위험이 있어 '이름→GR이 하나로
#           유일할 때만' 매칭합니다.
#
# 이 백필이 채워지면 관세청 통관조회(관부가세 결재통보 감지 → 8번 자동문자)의 대상이
# 11건 → 수천 건으로 늘어납니다.
# ==========================================================

import re

from integrations import quickstar_automation, quickstar_client
from repositories import order_repository


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def backfill_gr_numbers(max_pages: int = 200, progress=None) -> dict:
    """퀵스타 신청내역을 읽어 GR번호를 주문에 백필합니다.
    반환: {ok, apps, orders_scanned, matched_invoice, matched_name, updated, pages, page_links}
          또는 {ok:False, need_login, msg}."""
    listing = quickstar_automation.fetch_all_applications(max_pages=max_pages, progress=progress)
    if not listing.get("ok"):
        return {"ok": False, "need_login": listing.get("need_login", False),
                "msg": listing.get("msg", "퀵스타 신청내역 조회에 실패했습니다.")}

    apps = listing.get("rows") or []

    # 운송장 → GR (신청내역이 최신순이므로 먼저 나온 것을 유지), 이름 → GR목록
    inv_to_gr = {}
    name_to_grs = {}
    for a in apps:
        gr = a.get("gr")
        inv = _digits(a.get("invoice"))
        name = (a.get("name") or "").strip()
        if gr and inv:
            inv_to_gr.setdefault(inv, gr)
        if gr and name:
            name_to_grs.setdefault(name, []).append(gr)

    orders = order_repository.list_orders_missing_gr()
    matched_invoice = 0
    matched_name = 0
    updated = 0

    for o in orders:
        gr = None
        matched_inv_value = None
        # 1순위: 운송장 매칭 (우리가 등록한 것 → 쿠팡이 알려준 것 순)
        for field in ("invoice_number", "market_invoice_number"):
            d = _digits(o.get(field))
            if d and d in inv_to_gr:
                gr = inv_to_gr[d]
                matched_inv_value = d
                matched_invoice += 1
                break
        # 2순위: 수취인명 (이름→GR이 유일할 때만)
        if not gr:
            name = (o.get("receiver_name") or "").strip()
            grs = name_to_grs.get(name)
            if name and grs and len(set(grs)) == 1:
                gr = grs[0]
                matched_name += 1

        if gr:
            order_repository.backfill_quickstar_order_no(o["id"], gr, matched_inv_value)
            updated += 1

    return {
        "ok": True,
        "apps": len(apps),
        "orders_scanned": len(orders),
        "matched_invoice": matched_invoice,
        "matched_name": matched_name,
        "updated": updated,
        "pages": listing.get("pages"),
        "page_links": listing.get("page_links"),
    }


def _phone_key(value) -> str:
    """전화번호 비교용 정규화: 숫자만 추린 뒤 마지막 8자리(010/+82-10 접두 차이 무시)."""
    d = _digits(value)
    return d[-8:] if len(d) >= 8 else d


def _name_key(value) -> str:
    """이름 비교용 정규화: 모든 공백 제거 + 영문 대문자화.
    → '홍 길동'='홍길동', 'AU PHUONG THY'='auphuongthy'='AuPhuongThy' 로 취급."""
    return re.sub(r"\s+", "", str(value or "")).upper()


def _order_keys(o: dict):
    """주문에서 매칭에 쓸 키들: (수령자명, 구매자명, 전화집합, 통관고유부호).
    전화는 0502 안심번호(receiver_phone)뿐 아니라 실전화(customs_phone)·구매자전화까지 모아
    퀵스타 배대지의 실전화와 맞춰봅니다. pccc(통관고유부호)는 동명이인 구분의 확실한 키."""
    recv = (o.get("receiver_name") or "").strip()
    buyer = (o.get("orderer_name") or "").strip()
    phones = {_phone_key(p) for p in (o.get("receiver_phone"), o.get("orderer_phone"),
                                      o.get("customs_phone")) if _phone_key(p)}
    pccc = (o.get("pccc") or "").strip().upper()
    return recv, buyer, phones, pccc


def _pick_gr_by_identity(grs: list, o_phones: set, o_pccc: str) -> str:
    """동명이인(같은 이름 GR 여러 개)에서 정확히 1건을 고릅니다. 우선순위:
    ① 통관고유부호(personalNum) 일치  ② 전화(receiverPhone) 일치. 정확히 하나면 그 GR."""
    rec = {}  # gr -> (pccc, phonekey)
    for gr in grs:
        try:
            data = (quickstar_client.fetch_application(gr) or {}).get("data") or {}
        except Exception:  # noqa: BLE001
            continue
        ri = (data.get("RecInfo") or [{}])[0]
        rec[gr] = ((ri.get("personalNum") or "").strip().upper(), _phone_key(ri.get("receiverPhone")))
    if o_pccc:
        hit = [g for g, (pc, _ph) in rec.items() if pc and pc == o_pccc]
        if len(hit) == 1:
            return hit[0]
    if o_phones:
        hit = [g for g, (_pc, ph) in rec.items() if ph and ph in o_phones]
        if len(hit) == 1:
            return hit[0]
    return None


def backfill_gr_for_ready_to_ship(days_back: int = 3, progress=None, only_order_ids=None) -> dict:
    """발송대기 주문(아직 운송장 없음)에 GR번호를 채웁니다.
      - 퀵스타 신청내역을 **신청일 기준 최근 days_back일(기본 3일)**만 읽습니다(빠름).
      - only_order_ids가 주어지면 그 주문들만 채웁니다(표에서 체크한 것만). 없으면 발송대기 전체.
      - 1차: 신청내역 목록의 이름으로 **수령자명 먼저→없으면 구매자명** 매칭(한글 이름 빠르게).
      - 2차: 1차에서 못 찾은 주문은, 남은 신청서를 배대지 조회 API로 열어 **정확한 수취인명
             (외국인/영문 포함)**로 매칭. (목록 파서가 한글만 뽑아 외국인 이름 놓치는 문제 해결)
      - 같은 이름이 여러 건이면(동명이인) **통관고유부호(pccc)→전화**로 1건 확정, 아니면 건너뜀.
      - 이미 다른 주문에 배정된 GR은 후보에서 제외(중복 배정 방지).
    반환: {ok, apps, orders_scanned, matched_receiver, matched_buyer, matched_recinfo,
           ambiguous, no_match, updated, pages, days_back} 또는 {ok:False, need_login, msg}."""
    listing = quickstar_automation.fetch_all_applications(days_back=days_back, progress=progress)
    if not listing.get("ok"):
        return {"ok": False, "need_login": listing.get("need_login", False),
                "msg": listing.get("msg", "퀵스타 신청내역 조회에 실패했습니다.")}

    apps = listing.get("rows") or []
    used_grs = order_repository.get_used_gr_set()  # 이미 배정된 GR 제외
    # 이번 목록에서 아직 배정 안 된 GR들
    avail_grs = list(dict.fromkeys(a["gr"] for a in apps if a.get("gr") and a["gr"] not in used_grs))
    # 목록 파서가 뽑은 이름(주로 한글) → [gr]. 키는 정규화(공백/대소문자 무시).
    name_to_grs = {}
    for a in apps:
        gr, name = a.get("gr"), _name_key(a.get("name"))
        if gr and name and gr not in used_grs:
            name_to_grs.setdefault(name, []).append(gr)

    orders = order_repository.list_ready_to_ship_missing_gr()
    if only_order_ids is not None:
        _ids = set(only_order_ids)
        orders = [o for o in orders if o["id"] in _ids]
    matched_receiver = matched_buyer = matched_recinfo = 0
    ambiguous = no_match = updated = 0
    assigned = set()   # 이번 실행에서 배정한 GR(중복 방지)
    unmatched = []     # 1차에서 못 찾은 주문 → 2차로

    # ---- 1차: 목록 이름으로 매칭(수령자→구매자) ----
    for o in orders:
        recv, buyer, o_phones, o_pccc = _order_keys(o)
        gr = matched_by = None
        for nm, tag in ((recv, "receiver"), (buyer, "buyer")):
            nk = _name_key(nm)
            if not nk:
                continue
            grs = [g for g in dict.fromkeys(name_to_grs.get(nk, [])) if g not in assigned]
            if not grs:
                continue
            if len(grs) == 1:
                gr, matched_by = grs[0], tag
                break
            picked = _pick_gr_by_identity(grs, o_phones, o_pccc)  # 동명이인 → 통관번호/전화로 확정
            if picked and picked not in assigned:
                gr, matched_by = picked, tag
                break
        if gr:
            order_repository.backfill_quickstar_order_no(o["id"], gr, None)
            assigned.add(gr)
            updated += 1
            matched_receiver += (matched_by == "receiver")
            matched_buyer += (matched_by == "buyer")
        else:
            unmatched.append((o, recv, buyer, o_phones, o_pccc))

    # ---- 2차: 남은 주문 → 남은 신청서 RecInfo(정확한 이름+전화)로 매칭(외국인 이름 등) ----
    if unmatched:
        remaining = [g for g in avail_grs if g not in assigned]
        rec_by_gr = {}  # gr -> (수취인명, 전화키, 통관번호)
        for gr in remaining:
            try:
                data = (quickstar_client.fetch_application(gr) or {}).get("data") or {}
            except Exception:  # noqa: BLE001
                continue
            ri = (data.get("RecInfo") or [{}])[0]
            rec_by_gr[gr] = ((ri.get("receiverName") or "").strip(),
                             _phone_key(ri.get("receiverPhone")),
                             (ri.get("personalNum") or "").strip().upper())
        recname_to_grs = {}
        for gr, (nm, _ph, _pc) in rec_by_gr.items():
            nk = _name_key(nm)
            if nk:
                recname_to_grs.setdefault(nk, []).append(gr)

        for (o, recv, buyer, o_phones, o_pccc) in unmatched:
            gr = matched_by = None
            saw_name = False
            for nm, tag in ((recv, "receiver"), (buyer, "buyer")):
                nk = _name_key(nm)
                if not nk:
                    continue
                grs = [g for g in dict.fromkeys(recname_to_grs.get(nk, [])) if g not in assigned]
                if not grs:
                    continue
                saw_name = True
                if len(grs) == 1:
                    # 단일 이름 → 바로 매칭. 0502 안심번호 등으로 전화가 달라도 최근 7일+이름 유일이면 확실.
                    gr, matched_by = grs[0], tag
                    break
                # 동명이인 → 통관번호(pccc)→전화로 정확히 1건 확정
                hit = [g for g in grs if o_pccc and rec_by_gr[g][2] and rec_by_gr[g][2] == o_pccc]
                if len(hit) != 1:
                    hit = [g for g in grs if rec_by_gr[g][1] and rec_by_gr[g][1] in o_phones]
                if len(hit) == 1:
                    gr, matched_by = hit[0], tag
                    break
            if gr:
                order_repository.backfill_quickstar_order_no(o["id"], gr, None)
                assigned.add(gr)
                updated += 1
                matched_recinfo += 1
            elif saw_name:
                ambiguous += 1  # 이름은 찾았는데 전화 안 맞거나 여럿
            else:
                no_match += 1

    return {
        "ok": True,
        "apps": len(apps),
        "orders_scanned": len(orders),
        "matched_receiver": matched_receiver,
        "matched_buyer": matched_buyer,
        "matched_recinfo": matched_recinfo,
        "ambiguous": ambiguous,
        "no_match": no_match,
        "updated": updated,
        "pages": listing.get("pages"),
        "days_back": days_back,
    }
