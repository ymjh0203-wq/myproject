# ==========================================================
# 문자 발송 라우터 (services/sms_sender.py)
# ----------------------------------------------------------
# 앱에서 나가는 모든 문자(수동 발송 + 통관/우편 오류·관부가세 자동발송)는 여기를
# 거칩니다. 설정에 따라 발송 경로를 하나로 골라줍니다:
#   - config.SMS_GATEWAY_URL 이 있으면  -> 폰 SMS 게이트웨이(Tailscale)로 발송
#   - 비어 있으면                        -> 예전처럼 Windows 'Phone Link'로 발송
# 실패하면 PhoneLinkError를 냅니다(두 경로 공통). 호출하는 쪽은 경로를 몰라도 됩니다.
# ==========================================================

import config
from integrations import phone_link_sender, sms_gateway_sender
from integrations.phone_link_sender import PhoneLinkError  # 재노출(호출부 편의)

__all__ = ["send_message", "PhoneLinkError", "current_route"]


def current_route() -> str:
    """지금 어떤 경로로 문자가 나가는지 알려줍니다('gateway' 또는 'phone_link')."""
    return "gateway" if (config.SMS_GATEWAY_URL or "").strip() else "phone_link"


def send_message(phone: str, message: str) -> None:
    """설정된 경로로 문자를 보냅니다. 실패하면 PhoneLinkError."""
    if current_route() == "gateway":
        sms_gateway_sender.send_message(phone, message)
    else:
        phone_link_sender.send_message(phone, message)
