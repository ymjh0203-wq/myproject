import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent.parent      # coupang_finder/app
PROJECT_ROOT = APP_DIR.parent                          # coupang_finder/
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "app.db"
LOG_DIR = PROJECT_ROOT / "logs"

load_dotenv(PROJECT_ROOT / ".env")


_BOM = "﻿"


def _clean_env_value(raw: str) -> str:
    """메모장 등에서 저장할 때 섞여 들어갈 수 있는 BOM/공백 문자를 제거한다."""
    if not raw:
        return raw
    return raw.strip().replace(_BOM, "")


COUPANG_ACCESS_KEY = _clean_env_value(os.environ.get("COUPANG_ACCESS_KEY", ""))
COUPANG_SECRET_KEY = _clean_env_value(os.environ.get("COUPANG_SECRET_KEY", ""))

# 쿠팡파트너스(어필리에이트) Open API
PARTNERS_API_HOST = "https://api-gateway.coupang.com"
PARTNERS_SEARCH_PATH = "/v2/providers/affiliate_open_api/apis/openapi/products/search"
PARTNERS_SEARCH_MAX_LIMIT = 50          # 키워드당 최대 상품 수 (확인됨)
PARTNERS_SEARCH_HOURLY_LIMIT = 10       # 검색 API 시간당 최대 호출 횟수 (확인됨)

KIPRIS_SERVICE_KEY = _clean_env_value(os.environ.get("KIPRIS_SERVICE_KEY", ""))

# KIPRIS Plus 상표정보검색 API (KIPO 게이트웨이, getWordSearch — 폐기예정이지만
# 대체 상표 검색 오퍼레이션이 명확해질 때까지 우선 사용. 실제 응답 필드는 확인 완료.
KIPRIS_TRADEMARK_SEARCH_URL = "http://plus.kipris.or.kr/kipo-api/kipi/trademarkInfoSearchService/getWordSearch"

# 설계서 11장 "구현 전 확인할 질문"의 기본값
DEFAULT_SETTINGS = {
    "daily_max_analyze": "150",           # 하루 최대 분석 상품 수 (100~200 권장)
    "api_request_interval_sec": "450",    # 파트너스 API 요청 간격(초). 3600/450=시간당 8회
    "recheck_days_normal": "30",          # 일반 유망상품 재검사 주기(일)
    "recheck_days_s_grade": "14",         # S등급 재검사 주기(일)
    "category_max_pages": "5",            # 카테고리별 최대 탐색 페이지
    "wing_check_mode": "manual",          # manual(수동 보조) | auto(브라우저 자동화, 옵트인)
    "hyoja_auto_exclude_repeat": "5",     # 효자상품 자동 제외 임계치(동일판매자 반복횟수)
}
