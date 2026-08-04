# ==========================================================
# 개인정보 마스킹 (services/privacy.py)
# ----------------------------------------------------------
# 전화번호, 개인통관고유부호(PCCC) 같은 민감정보를 화면에 표시할 때
# 기본적으로 가운데를 가려서 보여주는 함수입니다.
# ==========================================================


def format_phone(phone: str) -> str:
    """
    숫자만 들어온 전화번호(예: 01012345678)를 보기 좋게 하이픈을 넣어줍니다.
    (쿠팡이 통관용 전화번호를 하이픈 없이 주는 경우가 있어서 필요합니다)
    이미 하이픈이 있거나 형식을 모르겠으면 원래 값을 그대로 돌려줍니다.
    """
    if not phone:
        return ""
    if "-" in phone:
        return phone
    digits = "".join(ch for ch in phone if ch.isdigit())
    # 안심번호(0502, 0503 등)는 앞자리가 4개입니다.
    if digits.startswith("050") and len(digits) >= 11:
        return f"{digits[:4]}-{digits[4:8]}-{digits[8:]}"
    if len(digits) == 11:  # 01012345678
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    if len(digits) == 10:  # 0212345678 / 01012345678이 아닌 경우
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return phone


def mask_phone(phone: str) -> str:
    """
    010-1234-5678 -> 010-****-5678 형태로 가운데만 가립니다.
    앞자리(010 / 0502 등)는 어떤 번호인지 구분할 수 있어야 해서 가리지 않습니다.
    """
    if not phone:
        return "-"
    formatted = format_phone(phone)
    parts = formatted.split("-")
    if len(parts) == 3:
        return f"{parts[0]}-{'*' * len(parts[1])}-{parts[2]}"
    if len(formatted) <= 4:
        return "*" * len(formatted)
    # 하이픈 형식을 못 알아본 경우에도, 앞 3자리와 뒤 4자리는 보여줍니다.
    if len(formatted) > 7:
        return f"{formatted[:3]}{'*' * (len(formatted) - 7)}{formatted[-4:]}"
    return "*" * (len(formatted) - 4) + formatted[-4:]


def mask_pccc(pccc: str) -> str:
    """P123456789012 -> P********9012 형태로 앞 1자리, 뒤 4자리만 보여줍니다."""
    if not pccc:
        return "-"
    if len(pccc) <= 5:
        return "*" * len(pccc)
    return pccc[0] + "*" * (len(pccc) - 5) + pccc[-4:]
