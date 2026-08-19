# ============================================================
# keywords_source.py  —  AI 소싱용 키워드 소스 3가지
# ------------------------------------------------------------
#   ① season_keywords(month)  : 큐레이션 시즌 캘린더(무료, 키 불필요)
#   ② autocomplete(seed)      : 네이버 자동완성 연관 키워드(무료, 키 불필요)
#   ③ ad_keywords(seed)       : 네이버 검색광고 키워드도구(월간검색수·경쟁도)
#                               → .env 에 키가 있어야 동작(무료 발급)
# ============================================================

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------
# ① 시즌 캘린더 (큐레이션) — 그 달에 잘 팔리는 시즌 상품 키워드
#    앱에서 회원님이 수정/추가할 수 있게 나중에 data/ 로 뺄 수 있음.
# ------------------------------------------------------------
SEASON_KEYWORDS = {
    1: ["홈트레이닝", "요가매트", "폼롤러", "전기요", "가습기", "핫팩", "방한장갑",
        "히터", "설날선물세트", "다이어리", "플래너", "스트레칭기구"],
    2: ["졸업선물", "입학선물", "책상", "책가방", "발렌타인포장", "봄자켓",
        "미세먼지마스크", "공기청정기", "환절기", "가디건", "운동화"],
    3: ["봄옷", "트렌치코트", "자전거", "등산용품", "캠핑용품", "피크닉매트",
        "황사마스크", "화분", "원예용품", "봄원피스", "미세먼지"],
    4: ["피크닉", "돗자리", "캠핑의자", "텐트", "킥보드", "봄원피스",
        "선글라스", "운동화", "자전거", "나들이가방", "벚꽃소품"],
    5: ["어린이날선물", "어버이날선물", "카네이션", "선크림", "양산",
        "아이스박스", "캠핑용품", "물놀이", "운동화", "가정의달"],
    6: ["제습기", "제습제", "우산", "레인부츠", "선풍기", "여름이불",
        "모기장", "살충제", "쿨토시", "냉감티셔츠"],
    7: ["튜브", "수영복", "물안경", "아이스박스", "쿨매트", "냉감이불",
        "서큘레이터", "선풍기", "워터파크", "래시가드", "캠핑용품"],
    8: ["실내운동기구", "요가매트", "스쿼트랙", "턱걸이기구", "캠핑용품",
        "서큘레이터", "냉감매트", "개학준비", "책가방", "물놀이용품", "다이어트기구"],
    9: ["추석선물세트", "환절기", "가디건", "자켓", "캠핑용품", "등산용품",
        "가을이불", "홈웨어", "가습기", "제습제", "명절포장"],
    10: ["캠핑난로", "차량용어닝", "대형텐트", "전기요", "히터", "침낭",
         "등산화", "김장용품", "가을코트", "부츠", "온수매트", "화로대"],
    11: ["히터", "온수매트", "전기요", "패딩", "방한용품", "가습기",
         "김장용품", "코트", "부츠", "블랙프라이데이", "목도리"],
    12: ["크리스마스트리", "크리스마스장식", "연말선물", "패딩", "목도리",
         "방한장갑", "히터", "가습기", "다이어리", "핫팩", "송년파티용품"],
}


def season_keywords(month: int) -> list[str]:
    """해당 월(1~12)의 시즌 추천 키워드 목록."""
    return SEASON_KEYWORDS.get(month, [])


# ------------------------------------------------------------
# ② 네이버 자동완성 연관 키워드 (무료, 키 불필요)
# ------------------------------------------------------------
def autocomplete(seed: str, limit: int = 15) -> list[str]:
    """씨앗 키워드의 네이버 자동완성 연관 키워드를 돌려줍니다."""
    seed = (seed or "").strip()
    if not seed:
        return []
    url = "https://ac.search.naver.com/nx/ac?" + urllib.parse.urlencode({
        "q": seed, "con": 1, "frm": "nv", "ans": 2,
        "r_format": "json", "r_enc": "UTF-8", "r_unicode": 0,
        "st": 100, "q_enc": "UTF-8",
    })
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.naver.com/"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    out = []
    for group in data.get("items", []):
        for row in group:
            if row and row[0] and row[0] not in out:
                out.append(row[0])
    return out[:limit]


# ------------------------------------------------------------
# ③ 네이버 검색광고 키워드도구 (월간검색수·경쟁도) — .env 키 필요
#    .env:
#      NAVER_AD_API_KEY=...       (액세스 라이선스)
#      NAVER_AD_SECRET=...        (비밀키)
#      NAVER_AD_CUSTOMER_ID=...   (고객 ID)
# ------------------------------------------------------------
def _ad_credentials():
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    return (
        os.getenv("NAVER_AD_API_KEY"),
        os.getenv("NAVER_AD_SECRET"),
        os.getenv("NAVER_AD_CUSTOMER_ID"),
    )


def has_ad_api() -> bool:
    """검색광고 API 키가 .env 에 설정돼 있는지."""
    return all(_ad_credentials())


def _sign(secret: str, timestamp: str, method: str, uri: str) -> str:
    msg = f"{timestamp}.{method}.{uri}"
    digest = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def ad_keywords(seed: str) -> list[dict] | None:
    """
    검색광고 키워드도구로 연관 키워드 + 월간검색수(PC/모바일) + 경쟁도를 돌려줍니다.
    키가 없으면 None 을 돌려줍니다(페이지에서 안내).
    반환: [{"keyword","pc","mobile","total","comp"} ...]
    """
    key, secret, cid = _ad_credentials()
    if not (key and secret and cid):
        return None

    uri = "/keywordstool"
    method = "GET"
    ts = str(round(time.time() * 1000))
    query = urllib.parse.urlencode({"hintKeywords": seed.strip(), "showDetail": 1})
    url = f"https://api.searchad.naver.com{uri}?{query}"
    req = urllib.request.Request(url, headers={
        "X-Timestamp": ts,
        "X-API-KEY": key,
        "X-Customer": str(cid),
        "X-Signature": _sign(secret, ts, method, uri),
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)

    def _num(v):
        # '< 10' 같은 문자열도 있으므로 숫자만 추출
        try:
            return int(str(v).replace("<", "").strip())
        except Exception:
            return 0

    rows = []
    for item in data.get("keywordList", [])[:50]:
        pc = _num(item.get("monthlyPcQcCnt"))
        mo = _num(item.get("monthlyMobileQcCnt"))
        rows.append({
            "keyword": item.get("relKeyword"),
            "pc": pc,
            "mobile": mo,
            "total": pc + mo,
            "comp": item.get("compIdx", ""),
        })
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows
