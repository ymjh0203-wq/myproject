# ==========================================================
# 통관정보 검사 업무 로직 (services/customs_service.py)
# ----------------------------------------------------------
# "검증" 버튼을 눌렀을 때 실제로 일어나는 일을 담당합니다.
#   1. 저장된 수취인/통관정보를 꺼내서
#   2. 검증기(LocalFormatValidator, UnipassCustomsValidator 등)에게 검사를 맡기고
#   3. 결과를 shipping_information에 저장 + customs_validations 이력에 남긴다
# ==========================================================

import hashlib

import models
from integrations.customs_validator import LocalFormatValidator, UnipassCustomsValidator, ValidationInput
from repositories import order_repository
from services import sms_templates
from services.sms_sender import PhoneLinkError, send_message

# 검증 결과의 '항목별 오류메시지'가 채워졌을 때, 어떤 오류문구를 보낼지 짝지어 둡니다.
# (통관번호를 우편번호보다 먼저 봅니다. 둘 다 틀린 경우 통관번호 오류문구를 보냅니다.)
_ERROR_FIELD_TEMPLATES = [
    ("pccc_message", "customs_error"),   # 통관고유부호(통관번호) 불일치 → 1. 통관번호 오류문구
    ("zip_message", "zipcode_error"),    # 우편번호 불일치            → 2. 우편번호 오류문구
]


def _build_snapshot_hash(name: str, phone: str, pccc: str, zip_code: str) -> str:
    """
    이름/전화번호/통관고유부호/우편번호 네 가지를 하나로 합쳐서 지문값(해시)을 만듭니다.
    나중에 이 값이 바뀌면 정보가 수정됐다는 뜻이므로 "재검증 필요" 판단에 씁니다.
    """
    normalized = "|".join(
        [
            (name or "").strip(),
            (phone or "").strip(),
            (pccc or "").strip().upper(),
            (zip_code or "").strip(),
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _build_validation_input(shipping: dict, name: str = None) -> ValidationInput:
    # 쿠팡이 통관용 실제 전화번호(customs_phone)를 이미 알려준 경우 그걸 우선 씁니다.
    # 없으면(예: Mock 데이터) 원본 전화번호로 검사해서 알아냅니다.
    phone = shipping.get("customs_phone") or shipping.get("receiver_phone_raw")
    return ValidationInput(
        receiver_name=name if name is not None else shipping.get("receiver_name"),
        phone_raw=phone,
        pccc=shipping.get("pccc"),
        zip_code=shipping.get("zip_code"),
    )


def _save_result(order_id: int, shipping: dict, result) -> None:
    snapshot_hash = _build_snapshot_hash(
        shipping.get("receiver_name"),
        shipping.get("customs_phone") or shipping.get("receiver_phone_raw"),
        shipping.get("pccc"),
        shipping.get("zip_code"),
    )

    order_repository.update_validation_status(
        order_id,
        status=result.status_code,
        message=result.message,
        validated_at=result.validated_at,
        snapshot_hash=snapshot_hash,
        customs_phone=result.used_phone,
        name_message=result.name_message,
        phone_message=result.phone_message,
        pccc_message=result.pccc_message,
        zip_message=result.zip_message,
    )
    order_repository.record_customs_validation(
        order_id,
        result.validator_type,
        result.is_success,
        result.status_code,
        result.message,
        result.retryable,
    )


def run_local_format_check(order: dict) -> None:
    """주문 하나에 대해 로컬 형식검사(LocalFormatValidator)를 실행하고 결과를 저장합니다."""
    shipping = order["shipping"] or {}
    validator = LocalFormatValidator()
    result = validator.validate(_build_validation_input(shipping))
    _save_result(order["id"], shipping, result)


def run_official_check(order: dict):
    """
    주문 하나에 대해 관세청 유니패스 공식 검증(UnipassCustomsValidator)을 실행하고
    결과를 저장합니다. 실제로 관세청 서버에 요청을 보내는 함수라, 인터넷 연결과
    .env의 UNIPASS_API_KEY가 필요합니다. 검증 결과(ValidationResult)를 돌려줍니다.

    통관고유부호는 실제로 등록한 사람(수령자 또는 구매자, 둘 중 누구 이름으로
    등록했는지는 주문마다 다를 수 있음) 이름과 일치해야 합니다. 그래서 수령자
    이름으로 먼저 검증하고, 불일치가 나면서 구매자 이름이 따로 있고 수령자와
    다르면 구매자 이름으로 한 번 더 검증합니다.
    """
    shipping = order["shipping"] or {}
    validator = UnipassCustomsValidator()

    receiver_name = (shipping.get("receiver_name") or "").strip()
    orderer_name = (order.get("orderer_name") or "").strip()

    result = validator.validate(_build_validation_input(shipping, receiver_name))

    if (
        result.status_code == models.VALIDATION_STATUS_OFFICIAL_MISMATCH
        and orderer_name
        and orderer_name != receiver_name
    ):
        retry_result = validator.validate(_build_validation_input(shipping, orderer_name))
        if retry_result.status_code == models.VALIDATION_STATUS_OFFICIAL_PASSED:
            retry_result.message = f"(수령자 대신 구매자 이름으로 재검증하여 일치) {retry_result.message}"
            result = retry_result

    _save_result(order["id"], shipping, result)
    return result


def maybe_send_customs_error_sms(order: dict, result) -> dict:
    """
    검증 결과가 통관번호/우편번호 불일치면, 해당 오류문구(1·2번)를 고객에게 자동으로
    문자 발송합니다. 같은 정보(이름·전화·통관번호·우편번호 스냅샷)로는 한 번만 보냅니다.

    반환(dict):
      - {"sent": True,  "field": "pccc"/"zip", "template": key}          발송함
      - {"sent": False, "reason": "no_error"/"already_sent"/"no_phone"}  안 보냄
      - {"sent": False, "reason": "send_failed", "error": 메시지}         발송 시도했으나 실패
    """
    # 어떤 항목 오류인지 판별 (통관번호 우선). 이름/전화 오류는 자동발송 대상이 아닙니다.
    template_key = None
    field = None
    for attr, tkey in _ERROR_FIELD_TEMPLATES:
        if getattr(result, attr, None):
            template_key = tkey
            field = "pccc" if attr == "pccc_message" else "zip"
            break
    if not template_key:
        return {"sent": False, "reason": "no_error"}

    shipping = order["shipping"] or {}
    phone = shipping.get("customs_phone") or shipping.get("receiver_phone_raw")
    if not phone:
        return {"sent": False, "reason": "no_phone", "field": field}

    snapshot = _build_snapshot_hash(
        shipping.get("receiver_name"),
        phone,
        shipping.get("pccc"),
        shipping.get("zip_code"),
    )
    # 중복발송 방지: 같은 정보(스냅샷)로 이미 보냈으면 건너뜁니다.
    if order_repository.get_customs_error_sms_sent(order["id"]) == snapshot:
        return {"sent": False, "reason": "already_sent", "field": field}

    message = sms_templates.render_template(template_key, order)
    try:
        send_message(phone, message)
    except PhoneLinkError as error:
        return {"sent": False, "reason": "send_failed", "field": field, "error": error.message}

    # 성공했을 때만 기록해서, 실패 시 다음 검증에서 다시 시도되게 합니다.
    order_repository.mark_customs_error_sms_sent(order["id"], snapshot)
    return {"sent": True, "field": field, "template": template_key}
