"""KIPRIS Plus 상표정보검색 API 클라이언트.

확인 필요: 정확한 엔드포인트/응답 필드는 실제 서비스키를 발급받은 뒤 KIPRIS Plus
문서로 다시 확인해야 한다. 지금은 공개 자료를 근거로 한 1차 구현이며, 실제 응답을
보고 필드명을 조정할 가능성이 높다 (Task: 실제 키로 응답 필드 검증).
"""

import xml.etree.ElementTree as ET

import requests

from app.config.settings import KIPRIS_SERVICE_KEY, KIPRIS_TRADEMARK_SEARCH_URL


class KiprisApiError(Exception):
    pass


class KiprisCredentialsMissing(KiprisApiError):
    pass


def search_trademark(word: str) -> list[dict]:
    """상표명(articleName)으로 검색해 출원/등록 상표 목록을 반환한다 (없으면 빈 리스트).

    응답 필드(확인됨): applicationNumber, title, applicationDate, registrationNumber,
    applicantName, drawing/bigDrawing, totalCount.
    """
    if not KIPRIS_SERVICE_KEY:
        raise KiprisCredentialsMissing(
            "KIPRIS_SERVICE_KEY가 설정되지 않았습니다. .env 파일에 값을 채워주세요 (.env.example 참고)."
        )

    response = requests.get(
        KIPRIS_TRADEMARK_SEARCH_URL,
        params={
            "articleName": word,
            "ServiceKey": KIPRIS_SERVICE_KEY,
            "searchYearRange": 0,
            "numOfRows": 20,
            "pageNo": 1,
        },
        timeout=10,
    )
    if response.status_code != 200:
        raise KiprisApiError(f"KIPRIS API 요청 실패 (HTTP {response.status_code}): {response.text[:300]}")

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise KiprisApiError(f"KIPRIS 응답을 해석할 수 없습니다: {exc}\n{response.text[:300]}") from exc

    items = []
    for item in root.iter("item"):
        entry = {child.tag: (child.text or "").strip() for child in item}
        items.append(entry)
    return items


def is_registered_trademark(word: str) -> bool:
    """word로 검색된 상표 중 등록번호(registrationNumber)가 있는 게 하나라도 있으면 True.

    출원만 되고 등록되지 않은 건은 제외한다. 조회 실패 시 조용히 False.
    """
    try:
        results = search_trademark(word)
    except KiprisApiError:
        return False

    return any(entry.get("registrationNumber") for entry in results)
