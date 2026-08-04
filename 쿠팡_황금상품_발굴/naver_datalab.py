# ==========================================================
# 네이버 오픈API - 데이터랩 (naver_datalab.py)
# ----------------------------------------------------------
# 키워드의 "최근 3년 월별 검색 추이"를 조회해서 계절성을 판단합니다.
# https://openapi.naver.com/v1/datalab/search 를 호출합니다.
#
# config.py에 Client ID / Client Secret이 채워져 있어야 실제로 조회가 됩니다.
# 비어있으면 조회를 건너뛰고 None을 돌려줍니다. (에러로 프로그램이 죽지 않음)
#
# 데이터랩은 "절대 검색량"이 아니라 0~100 사이의 "상대 비율(ratio)"을 줍니다.
# 그래서 이 파일에서는 달마다 비율이 얼마나 들쭉날쭉한지(변동계수)를 계산해서
# 숫자가 낮을수록(=1년 내내 고르게 팔림) "계절성이 적다"고 판단합니다.
# ==========================================================

import datetime
import json
import statistics
import urllib.request
import urllib.error

import config

API_URL = "https://openapi.naver.com/v1/datalab/search"


def is_configured():
    """데이터랩 API 키가 채워져 있는지 확인합니다."""
    return bool(config.NAVER_OPENAPI_CLIENT_ID and config.NAVER_OPENAPI_CLIENT_SECRET)


def get_seasonality(keyword):
    """
    키워드의 최근 N년(config.SEASONALITY_YEARS) 월별 검색 추이를 조회합니다.

    반환값: (결과 딕셔너리 또는 None, 상태 메시지)

    결과 딕셔너리 형태:
        {
            "변동계수": 0.35,          # 낮을수록 연중 고르게 팔림 (계절 영향 적음)
            "최고월": 7,               # 검색비율이 가장 높은 달 (1~12)
            "최고월_비율": 88.2,
            "최저월": 1,
            "최저월_비율": 12.5,
            "월별_평균비율": {1: 12.5, 2: 20.1, ...},
        }
    """
    if not is_configured():
        return None, "데이터랩 API 키가 설정되지 않아 계절성 조회를 건너뜀"

    end_date = datetime.date.today()
    start_date = end_date.replace(year=end_date.year - config.SEASONALITY_YEARS)

    body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "timeUnit": "month",
        "keywordGroups": [{"groupName": keyword, "keywords": [keyword]}],
    }

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "X-Naver-Client-Id": config.NAVER_OPENAPI_CLIENT_ID,
            "X-Naver-Client-Secret": config.NAVER_OPENAPI_CLIENT_SECRET,
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return None, f"데이터랩 API 오류 (HTTP {e.code}) - 키/권한을 확인하세요"
    except Exception as e:
        return None, f"데이터랩 API 호출 실패: {e}"

    results = data.get("results", [])
    if not results or not results[0].get("data"):
        return None, "계절성 데이터 없음"

    points = results[0]["data"]  # [{"period": "2023-07-01", "ratio": 12.3}, ...]

    # 같은 달(1~12월)끼리 비율을 모아서 평균을 냄
    monthly_values = {m: [] for m in range(1, 13)}
    for point in points:
        month = int(point["period"][5:7])
        monthly_values[month].append(point.get("ratio", 0))

    monthly_avg = {
        m: (round(statistics.mean(v), 1) if v else 0.0) for m, v in monthly_values.items()
    }

    values = list(monthly_avg.values())
    mean_value = statistics.mean(values)
    if mean_value == 0:
        variation_coefficient = 0.0
    else:
        stdev_value = statistics.pstdev(values)
        variation_coefficient = round(stdev_value / mean_value, 2)

    peak_month = max(monthly_avg, key=monthly_avg.get)
    low_month = min(monthly_avg, key=monthly_avg.get)

    result = {
        "변동계수": variation_coefficient,
        "최고월": peak_month,
        "최고월_비율": monthly_avg[peak_month],
        "최저월": low_month,
        "최저월_비율": monthly_avg[low_month],
        "월별_평균비율": monthly_avg,
    }
    return result, "조회 성공"
