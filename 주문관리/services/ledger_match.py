# ==========================================================
# 매출정리 수취인 대조 (services/ledger_match.py)
# ----------------------------------------------------------
# 배대지(퀵스타) 신청서의 '수취인명 + 전화번호'가 그 주문의 수취인과 맞는지 확인합니다.
# 둘 다 맞아야 '인증(일치)'으로 보고, 그때만 단가·배송비를 채웁니다.
#   → GR이 엉뚱한 사람 것으로 매칭돼도, 남의 단가·배송비가 섞여 들어가는 걸 막습니다.
# ==========================================================

import re


def digits(value) -> str:
    """전화번호에서 숫자만 남깁니다. (하이픈·공백·국가코드 표기 차이를 흡수)"""
    return re.sub(r"\D", "", str(value or ""))


def norm_name(value) -> str:
    """이름 비교용 정규화: 공백 제거."""
    return re.sub(r"\s+", "", str(value or "")).strip()


def phone_match(a: str, b: str) -> bool:
    """두 전화번호가 같은지. 뒤 8자리(국번 뒤)가 같으면 같은 번호로 봅니다
    (+82·앞자리 0 유무 등 표기 차이 흡수)."""
    a, b = digits(a), digits(b)
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) >= 8 and len(b) >= 8 and a[-8:] == b[-8:]


def receiver_of_application(data: dict):
    """배대지 신청서 응답(data)에서 (수취인명, 전화숫자)를 뽑습니다. 없으면 ('', '')."""
    rec = (data.get("RecInfo") or [{}])
    rec = rec[0] if rec else {}
    return norm_name(rec.get("receiverName")), digits(rec.get("receiverPhone"))


def verify(app_name: str, app_phone: str, order_name: str, order_phones) -> tuple:
    """배대지 수취인(app_*)과 주문 수취인(order_*)을 대조합니다.
    order_phones: 비교할 주문쪽 전화번호 후보 목록(통관전화·수취인전화 등).
    반환: (일치여부 bool, 사유 str). 일치면 사유는 ''.
    규칙: 이름과 전화가 '둘 다' 맞아야 일치. 어느 한쪽 정보가 없으면 불일치로 보고 사유를 남깁니다."""
    an = norm_name(app_name)
    on = norm_name(order_name)
    ophones = [p for p in (order_phones or []) if digits(p)]

    if not an and not app_phone:
        return False, "배대지에 수취인 정보 없음"
    if not on and not ophones:
        return False, "대조할 주문 수취인 정보 없음"

    name_ok = bool(an) and bool(on) and an == on
    phone_ok = bool(app_phone) and any(phone_match(app_phone, p) for p in ophones)

    if name_ok and phone_ok:
        return True, ""

    probs = []
    if not name_ok:
        probs.append(f"수취인 불일치(배대지 '{app_name or '?'}' / 주문 '{order_name or '?'}')")
    if not phone_ok:
        a4 = app_phone[-4:] if app_phone else "?"
        o4 = digits(ophones[0])[-4:] if ophones else "?"
        probs.append(f"전화 불일치(배대지 …{a4} / 주문 …{o4})")
    return False, " · ".join(probs)
