# ==========================================================
# 통관정보 검증 모듈 (integrations/customs_validator.py)
# ----------------------------------------------------------
# 통관정보(이름/전화번호/통관고유부호/우편번호)를 검사하는 방식이 여러 개 있을 수
# 있어서, 공통 인터페이스(BaseCustomsValidator)를 만들고 그 아래에 종류별로
# 구현합니다.
#
#   LocalFormatValidator   : 실제로 동작함. 관세청에 요청하지 않고,
#                            저장된 데이터의 '형식'만 검사합니다.
#   MockCustomsValidator   : 실제로 동작함. 관세청 대신 가짜로 성공/불일치/오류
#                            상황을 흉내냅니다. (개발·테스트용)
#   UnipassCustomsValidator: 실제로 동작함. 관세청 유니패스 공식 API(API028)로
#                            실제 이름·전화번호·통관고유부호·우편번호 일치 여부를
#                            확인합니다. (관세청 "OPEN API 연계가이드 v4.0" 기준)
# ==========================================================

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

import requests

import config
import models

SAFE_NUMBER_PREFIX = "050"  # 050으로 시작하면 실제 번호를 가려주는 '안심번호'입니다.
MOBILE_PATTERN = re.compile(r"^01[0-9]{8,9}$")
PCCC_PATTERN = re.compile(r"^P[0-9]{12}$")
ZIP_PATTERN = re.compile(r"^[0-9]{5}$")


def _normalize_phone(phone: str) -> str:
    """숫자만 남기고 하이픈/공백 등을 제거합니다."""
    return re.sub(r"[^0-9]", "", phone or "")


def _normalize_pccc(pccc: str) -> str:
    """앞뒤 공백을 지우고 대문자로 바꿉니다."""
    return (pccc or "").strip().upper()


def _normalize_zip(zip_code: str) -> str:
    return re.sub(r"[^0-9]", "", zip_code or "")


@dataclass
class ValidationInput:
    """검증기에 넣을 입력값입니다."""

    receiver_name: str
    phone_raw: str  # 마켓이 준 원본 전화번호 (안심번호일 수 있음)
    pccc: str
    zip_code: str


@dataclass
class ValidationResult:
    """검증 결과입니다."""

    is_success: bool
    status_code: str  # models.VALIDATION_STATUS_* 중 하나
    message: str
    validated_at: str
    validator_type: str
    retryable: bool
    used_phone: str = None  # 통관 검증에 실제로 사용한 전화번호 (정할 수 있었던 경우)
    # 항목별 검사 결과 메시지입니다. 문제가 없으면 None, 있으면 그 항목만의 메시지가 들어갑니다.
    # LocalFormatValidator와 UnipassCustomsValidator가 채워줍니다.
    # (유니패스는 연계가이드에 실제로 "우편번호가 일치하지 않습니다" 같은 항목별 오류
    # 메시지가 정의되어 있어서, 그 문구를 보고 항목을 구분합니다)
    # MockCustomsValidator는 항목을 구분하지 않는 가짜 검증이라 항상 비어있습니다.
    name_message: str = None
    phone_message: str = None
    pccc_message: str = None
    zip_message: str = None


class BaseCustomsValidator:
    """모든 검증기가 상속받는 공통 인터페이스입니다."""

    validator_type = "BaseCustomsValidator"

    def validate(self, data: ValidationInput) -> ValidationResult:
        raise NotImplementedError


class LocalFormatValidator(BaseCustomsValidator):
    """
    관세청에 실제로 요청하지 않고, 저장된 데이터의 '형식'만 검사합니다.
    이름/전화번호/통관고유부호가 실제로 일치하는지는 확인하지 않습니다
    (그건 MockCustomsValidator나 UnipassCustomsValidator의 역할입니다).
    """

    validator_type = "LocalFormatValidator"

    def validate(self, data: ValidationInput) -> ValidationResult:
        # 쿠팡은 해외구매대행 상품 결제 시 통관정보를 필수로 받기 때문에,
        # 값이 아예 비어있는 상황(정보누락)은 실제로는 일어나지 않는다고 보고
        # 별도 상태로 구분하지 않습니다. 값이 비어있어도 아래 형식 검사에서
        # 자연스럽게 걸러져서 "형식오류"로 처리됩니다.
        now = datetime.now().isoformat(timespec="seconds")
        used_phone = None

        name_message = None
        name = (data.receiver_name or "").strip()
        if len(name) < 2:
            name_message = "이름이 일치하지 않습니다."

        phone_message = None
        phone_digits = _normalize_phone(data.phone_raw)
        if phone_digits.startswith(SAFE_NUMBER_PREFIX) or not MOBILE_PATTERN.match(phone_digits):
            phone_message = "전화번호가 일치하지 않습니다."
        else:
            used_phone = phone_digits

        pccc_message = None
        pccc = _normalize_pccc(data.pccc)
        if not PCCC_PATTERN.match(pccc):
            pccc_message = "개인통관고유부호가 일치하지 않습니다."

        zip_message = None
        zip_digits = _normalize_zip(data.zip_code)
        if not ZIP_PATTERN.match(zip_digits):
            zip_message = "우편번호가 일치하지 않습니다."

        field_messages = [name_message, phone_message, pccc_message, zip_message]

        if not any(field_messages):
            return ValidationResult(
                is_success=True,
                status_code=models.VALIDATION_STATUS_FORMAT_PASSED,
                message="형식검사를 통과했습니다.",
                validated_at=now,
                validator_type=self.validator_type,
                retryable=False,
                used_phone=used_phone,
            )

        return ValidationResult(
            is_success=False,
            status_code=models.VALIDATION_STATUS_FORMAT_ERROR,
            message=" / ".join(msg for msg in field_messages if msg),
            validated_at=now,
            validator_type=self.validator_type,
            retryable=False,
            used_phone=used_phone,
            name_message=name_message,
            phone_message=phone_message,
            pccc_message=pccc_message,
            zip_message=zip_message,
        )


class MockCustomsValidator(BaseCustomsValidator):
    """
    실제 관세청 API를 호출하지 않고, 통관고유부호 마지막 숫자로
    성공 / 불일치 / 오류 상황을 흉내내는 가짜 검증기입니다. (개발·테스트용)
    """

    validator_type = "MockCustomsValidator"

    def validate(self, data: ValidationInput) -> ValidationResult:
        now = datetime.now().isoformat(timespec="seconds")
        pccc = _normalize_pccc(data.pccc)

        if not PCCC_PATTERN.match(pccc):
            return ValidationResult(
                is_success=False,
                status_code=models.VALIDATION_STATUS_REQUEST_ERROR,
                message="형식이 올바르지 않아 검증을 요청할 수 없습니다. 먼저 형식검사를 통과해야 합니다.",
                validated_at=now,
                validator_type=self.validator_type,
                retryable=False,
            )

        last_digit = int(pccc[-1])
        if last_digit in (0, 2, 4, 6, 8):
            return ValidationResult(
                is_success=True,
                status_code=models.VALIDATION_STATUS_OFFICIAL_PASSED,
                message="(Mock) 이름·전화번호·통관고유부호가 일치합니다.",
                validated_at=now,
                validator_type=self.validator_type,
                retryable=False,
            )
        if last_digit == 9:
            return ValidationResult(
                is_success=False,
                status_code=models.VALIDATION_STATUS_REQUEST_ERROR,
                message="(Mock) 검증 서버 응답 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                validated_at=now,
                validator_type=self.validator_type,
                retryable=True,
            )
        return ValidationResult(
            is_success=False,
            status_code=models.VALIDATION_STATUS_OFFICIAL_MISMATCH,
            message="(Mock) 입력한 정보와 관세청에 등록된 정보가 일치하지 않습니다.",
            validated_at=now,
            validator_type=self.validator_type,
            retryable=False,
        )


UNIPASS_ENDPOINT = "https://unipass.customs.go.kr:38010/ext/rest/persEcmQry/retrievePersEcm"


def _classify_unipass_error(message: str) -> str:
    """
    유니패스가 돌려준 오류 문구를 보고, 이름/전화번호/우편번호/통관고유부호 중
    어느 항목 문제인지 구분합니다. (연계가이드 v4.0, API028 예제 메시지 기준)
    """
    if "우편번호" in message:
        return "zip"
    if "전화번호" in message:
        return "phone"
    if "성명" in message or "납세의무자명" in message or "납세의무자(영문)명" in message:
        return "name"
    return "pccc"  # "존재하지 않습니다", "유효기간", "사용중이 아닌" 등은 부호 자체 문제로 분류


class UnipassCustomsValidator(BaseCustomsValidator):
    """
    관세청 유니패스 공식 API(API028: 수입신고 개인통관고유부호 검증)로 실제
    이름·전화번호·통관고유부호·우편번호가 관세청 등록 정보와 일치하는지 확인합니다.

    출처: 관세청 "OPEN API 연계가이드 v4.0" 3.2.28 개인통관고유부호 유효성검증
    - 요청: GET https://unipass.customs.go.kr:38010/ext/rest/persEcmQry/retrievePersEcm
            ?crkyCn=인증키&persEcm=통관고유부호&pltxNm=이름&cralTelno=전화번호&custPsno=우편번호
    - 응답(XML): tCnt (1=일치, 0=불일치, -1=시스템장애), errMsgCn(0..n, 불일치 사유)
    """

    validator_type = "UnipassCustomsValidator"

    def validate(self, data: ValidationInput) -> ValidationResult:
        now = datetime.now().isoformat(timespec="seconds")

        if not config.UNIPASS_API_KEY:
            return ValidationResult(
                is_success=False,
                status_code=models.VALIDATION_STATUS_REQUEST_ERROR,
                message="관세청 유니패스 인증키(.env의 UNIPASS_API_KEY)가 설정되지 않았습니다.",
                validated_at=now,
                validator_type=self.validator_type,
                retryable=False,
            )

        # 형식부터 틀린 데이터로 실제 관세청 서버를 호출하지 않도록 미리 걸러줍니다
        # (호출 횟수를 아끼고, 목적 외 사용으로 오해받을 여지도 줄입니다).
        name_ok = len((data.receiver_name or "").strip()) >= 2
        phone_ok = bool(MOBILE_PATTERN.match(_normalize_phone(data.phone_raw)))
        pccc_ok = bool(PCCC_PATTERN.match(_normalize_pccc(data.pccc)))
        if not (name_ok and phone_ok and pccc_ok):
            return ValidationResult(
                is_success=False,
                status_code=models.VALIDATION_STATUS_REQUEST_ERROR,
                message="형식이 올바르지 않아 검증을 요청할 수 없습니다. 먼저 형식검사를 통과해야 합니다.",
                validated_at=now,
                validator_type=self.validator_type,
                retryable=False,
            )

        params = {
            "crkyCn": config.UNIPASS_API_KEY,
            "persEcm": _normalize_pccc(data.pccc),
            "pltxNm": (data.receiver_name or "").strip(),
            "cralTelno": _normalize_phone(data.phone_raw),
        }
        zip_digits = _normalize_zip(data.zip_code)
        if zip_digits:
            params["custPsno"] = zip_digits

        try:
            response = requests.get(UNIPASS_ENDPOINT, params=params, timeout=15)
        except requests.exceptions.RequestException as error:
            return ValidationResult(
                is_success=False,
                status_code=models.VALIDATION_STATUS_REQUEST_ERROR,
                message=f"관세청 서버 통신 중 오류가 발생했습니다: {error}",
                validated_at=now,
                validator_type=self.validator_type,
                retryable=True,
            )

        if response.status_code != 200:
            return ValidationResult(
                is_success=False,
                status_code=models.VALIDATION_STATUS_REQUEST_ERROR,
                message=f"관세청 서버가 오류를 응답했습니다 (상태코드 {response.status_code}).",
                validated_at=now,
                validator_type=self.validator_type,
                retryable=response.status_code >= 500,
            )

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as error:
            return ValidationResult(
                is_success=False,
                status_code=models.VALIDATION_STATUS_REQUEST_ERROR,
                message=f"관세청 응답을 해석할 수 없습니다: {error}",
                validated_at=now,
                validator_type=self.validator_type,
                retryable=True,
            )

        t_cnt = (root.findtext("tCnt") or "").strip()
        error_messages = [el.text.strip() for el in root.findall(".//errMsgCn") if el.text]

        if t_cnt == "1":
            return ValidationResult(
                is_success=True,
                status_code=models.VALIDATION_STATUS_OFFICIAL_PASSED,
                message="관세청 등록 정보와 일치합니다.",
                validated_at=now,
                validator_type=self.validator_type,
                retryable=False,
            )

        if t_cnt == "0":
            field_messages = {"name": None, "phone": None, "zip": None, "pccc": None}
            for msg in error_messages:
                field = _classify_unipass_error(msg)
                # 같은 항목에 문구가 여러 개면 이어붙입니다.
                field_messages[field] = f"{field_messages[field]} / {msg}" if field_messages[field] else msg

            return ValidationResult(
                is_success=False,
                status_code=models.VALIDATION_STATUS_OFFICIAL_MISMATCH,
                message=" / ".join(error_messages) or "관세청 등록 정보와 일치하지 않습니다.",
                validated_at=now,
                validator_type=self.validator_type,
                retryable=False,
                name_message=field_messages["name"],
                phone_message=field_messages["phone"],
                pccc_message=field_messages["pccc"],
                zip_message=field_messages["zip"],
            )

        # t_cnt == "-1" (시스템장애) 이거나 예상 못한 값인 경우
        return ValidationResult(
            is_success=False,
            status_code=models.VALIDATION_STATUS_REQUEST_ERROR,
            message="관세청 시스템 장애로 검증할 수 없습니다. 잠시 후 다시 시도해주세요.",
            validated_at=now,
            validator_type=self.validator_type,
            retryable=True,
        )
