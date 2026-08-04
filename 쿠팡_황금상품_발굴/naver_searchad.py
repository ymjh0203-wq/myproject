# ==========================================================
# 네이버 검색광고 API (naver_searchad.py)
# ----------------------------------------------------------
# 키워드의 "월간 검색량(PC+모바일)"을 조회합니다.
# https://api.naver.com/keywordstool 를 호출합니다.
#
# config.py에 API 키 3개(API Key, Secret Key, Customer ID)가
# 모두 채워져 있어야 실제로 조회가 됩니다.
# 하나라도 비어있으면 조회를 건너뛰고 None을 돌려줍니다. (에러로 프로그램이 죽지 않음)
# ==========================================================

import base64
import hashlib
import hmac
import json
import time
import urllib.request
import urllib.parse
import urllib.error

import config

API_URL = "https://api.naver.com"
API_PATH = "/keywordstool"


def is_configured():
    """검색광고 API 키가 3개 다 채워져 있는지 확인합니다."""
    return bool(
        config.NAVER_SEARCHAD_API_KEY
        and config.NAVER_SEARCHAD_SECRET_KEY
        and config.NAVER_SEARCHAD_CUSTOMER_ID
    )


def _make_signature(timestamp, method, path, secret_key):
    """네이버 검색광고 API가 요구하는 서명값(signature)을 만듭니다."""
    message = f"{timestamp}.{method}.{path}"
    hashed = hmac.new(
        bytes(secret_key, "utf-8"), bytes(message, "utf-8"), hashlib.sha256
    )
    return base64.b64encode(hashed.digest()).decode()


def _to_number(value):
    """네이버는 검색량이 너무 적으면 '< 10' 같은 문자열을 주기도 함 -> 숫자로 안전 변환."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _call_keywordstool(seed_keyword):
    """
    keywordstool API를 호출해서 원본 응답(keywordList)을 그대로 돌려줍니다.
    get_monthly_search_volume, get_related_keywords가 공통으로 사용하는 내부 함수입니다.

    반환값: (keywordList 리스트 또는 None, 상태 메시지)
    """
    # 네이버 검색광고 API는 hintKeywords에 띄어쓰기가 있으면 오류(HTTP 400)를 낸다.
    # "에어렌치 공구"처럼 사람이 자연스럽게 입력한 키워드도 바로 동작하도록 자동으로 제거한다.
    seed_keyword = seed_keyword.replace(" ", "")

    timestamp = str(int(time.time() * 1000))
    signature = _make_signature(
        timestamp, "GET", API_PATH, config.NAVER_SEARCHAD_SECRET_KEY
    )

    query = urllib.parse.urlencode({"hintKeywords": seed_keyword, "showDetail": "1"})
    url = f"{API_URL}{API_PATH}?{query}"

    headers = {
        "X-Timestamp": timestamp,
        "X-API-KEY": config.NAVER_SEARCHAD_API_KEY,
        "X-Customer": str(config.NAVER_SEARCHAD_CUSTOMER_ID),
        "X-Signature": signature,
    }

    request = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return None, f"검색광고 API 오류 (HTTP {e.code}) - 키/권한을 확인하세요"
    except Exception as e:
        return None, f"검색광고 API 호출 실패: {e}"

    return data.get("keywordList", []), "조회 성공"


def get_monthly_search_volume(keyword):
    """
    키워드 하나의 월간 검색량 합계(PC+모바일)를 조회합니다.

    반환값:
        (검색량 숫자, 상태 메시지)
        API 키가 없거나 오류가 나면 (None, "사유 설명")을 돌려줍니다.
    """
    if not is_configured():
        return None, "검색광고 API 키가 설정되지 않아 검색량 조회를 건너뜀"

    keyword_list, status = _call_keywordstool(keyword)
    if keyword_list is None:
        return None, status
    if not keyword_list:
        return 0, "검색량 데이터 없음 (0으로 처리)"

    # 키워드와 정확히 일치하는 항목을 우선 찾고, 없으면 첫 번째 항목 사용
    normalized = keyword.replace(" ", "").upper()
    target = None
    for item in keyword_list:
        if item.get("relKeyword", "").replace(" ", "").upper() == normalized:
            target = item
            break
    if target is None:
        target = keyword_list[0]

    total = _to_number(target.get("monthlyPcQcCnt", 0)) + _to_number(
        target.get("monthlyMobileQcCnt", 0)
    )
    return total, "조회 성공"


def get_related_keywords(seed_keyword, max_results=None):
    """
    시드 키워드(예: '캠핑용품' 같은 대표/카테고리성 키워드)를 넣으면,
    네이버가 연관되어 있다고 판단하는 키워드들을 검색량/경쟁정도와 함께 전부 돌려줍니다.

    이게 바로 "키워드 발굴" 기능입니다. 데이터랩 쇼핑인사이트 화면을 스크래핑하지 않고도,
    공식 API로 비슷한 결과(분야 내 인기/연관 키워드 + 실제 검색량)를 얻을 수 있습니다.

    반환값: (결과 리스트 또는 None, 상태 메시지)

    결과 리스트의 각 항목:
        {
            "키워드": "캠핑테이블",
            "월간검색량": 12300,
            "PC검색량": 4000,
            "모바일검색량": 8300,
            "경쟁정도": "낮음",       # 낮음/중간/높음
            "월평균노출광고수": 3.2,
        }
    검색량이 많은 순서로 정렬되어 있습니다.
    """
    if not is_configured():
        return None, "검색광고 API 키가 설정되지 않아 연관키워드 조회를 건너뜀"

    keyword_list, status = _call_keywordstool(seed_keyword)
    if keyword_list is None:
        return None, status
    if not keyword_list:
        return [], "연관키워드 없음"

    results = []
    for item in keyword_list:
        pc = _to_number(item.get("monthlyPcQcCnt", 0))
        mobile = _to_number(item.get("monthlyMobileQcCnt", 0))
        results.append(
            {
                "키워드": item.get("relKeyword", ""),
                "월간검색량": pc + mobile,
                "PC검색량": pc,
                "모바일검색량": mobile,
                "경쟁정도": item.get("compIdx", "정보없음"),
                "월평균노출광고수": item.get("plAvgDepth", ""),
            }
        )

    results.sort(key=lambda r: r["월간검색량"], reverse=True)

    if max_results is not None:
        results = results[:max_results]

    return results, "조회 성공"
