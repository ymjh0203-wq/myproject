# ==========================================================
# 문자 발송 - 폰 SMS 게이트웨이 (integrations/sms_gateway_sender.py)
# ----------------------------------------------------------
# 폰에 설치한 "Simple SMS Gateway" 앱이 폰 안에서 작은 HTTP 서버를 띄우고,
# PC가 Tailscale(사설망)로 그 서버에 명령을 보내면 폰이 자기 유심으로 문자를
# 보냅니다. Phone Link(휴대폰과 연결)와 달리 폰이 PC 옆에 없어도, 폰에 인터넷만
# 있으면 원격에서 발송됩니다. 고객정보가 제3자 서버를 거치지 않습니다.
#
#   요청: POST {SMS_GATEWAY_URL}   본문 {"phone": "01012345678", "message": "..."}
#   응답: {"success": true, "message": "SMS sent successfully", "to": "..."}
#
# 실패하면 PhoneLinkError를 냅니다(기존 발송 코드가 그대로 처리하도록 같은 예외 재사용).
# ==========================================================

import requests

import config
from integrations.phone_link_sender import PhoneLinkError


def send_message(phone: str, message: str) -> None:
    """폰 SMS 게이트웨이로 문자를 보냅니다. 실패하면 PhoneLinkError를 냅니다."""
    url = (config.SMS_GATEWAY_URL or "").strip()
    if not url:
        raise PhoneLinkError("error", "문자 게이트웨이 주소(SMS_GATEWAY_URL)가 설정되지 않았습니다.")

    # 번호에서 숫자만 남깁니다(하이픈/공백 제거). 예: 010-7666-7336 -> 01076667336
    phone_digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if not phone_digits:
        raise PhoneLinkError("error", "받는 전화번호가 비어 있습니다.")

    try:
        response = requests.post(
            url,
            json={"phone": phone_digits, "message": message},
            timeout=20,
        )
    except requests.exceptions.RequestException as error:
        # 폰 앱이 꺼져 있거나(서버 미실행) Tailscale 연결이 끊긴 경우 등.
        raise PhoneLinkError(
            "error",
            "폰 문자앱에 연결하지 못했습니다. 폰의 'Simple SMS Gateway' 앱이 켜져 있는지"
            f"(Running), Tailscale이 연결돼 있는지 확인해주세요. ({error})",
        )

    if response.status_code != 200:
        raise PhoneLinkError("error", f"문자 게이트웨이 오류 (HTTP {response.status_code}): {response.text[:200]}")

    try:
        data = response.json()
    except ValueError:
        data = {}
    # success 키가 명시적으로 False면 실패로 봅니다(키가 없으면 200이므로 성공으로 간주).
    if data.get("success") is False:
        raise PhoneLinkError("error", data.get("message") or "폰에서 문자 발송에 실패했습니다.")
