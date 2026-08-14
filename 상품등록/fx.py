# ============================================================
# fx.py  —  실시간(일 단위) 환율 조회
# ------------------------------------------------------------
# 무료·키 불필요 API(frankfurter.app, ECB 기준)를 사용합니다.
# 한 번의 시계열 호출로 최신값 + 전일대비 변동까지 계산합니다.
# (ECB 기준환율이라 영업일 1일 단위로 갱신됩니다. 실매입가와는 차이가 있을 수 있음)
# ============================================================

import datetime as dt
import json
import urllib.request

# 표시할 통화 순서 (화면 카드 순서와 동일)
CURRENCIES = [
    ("CNY", "중국"),
    ("USD", "미국"),
    ("JPY", "일본"),
    ("EUR", "유럽"),
    ("GBP", "영국"),
    ("AUD", "호주"),
    ("CAD", "캐나다"),
    ("INR", "인도"),
]


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def get_rates() -> dict:
    """
    각 통화의 1단위당 원화값과 전일대비 변동(%)을 돌려줍니다.
    반환: {"date": "YYYY-MM-DD", "rows": [{"code","name","krw","change"}...]}
    """
    # EUR 는 기준통화라 rates 에 안 들어오므로, 나머지 + KRW 만 요청
    needed = ["KRW"] + [c for c, _ in CURRENCIES if c != "EUR"]
    syms = ",".join(needed)
    start = (dt.date.today() - dt.timedelta(days=8)).isoformat()
    data = _fetch(f"https://api.frankfurter.app/{start}..?to={syms}")

    by_date = data["rates"]
    dates = sorted(by_date.keys())
    latest, prev = dates[-1], dates[-2] if len(dates) >= 2 else dates[-1]

    def krw_of(code: str, date: str) -> float:
        row = by_date[date]
        krw = row["KRW"]           # 1 EUR = krw 원
        if code == "EUR":
            return krw
        return krw / row[code]     # 1 code = (1 EUR in KRW) / (1 EUR in code)

    rows = []
    for code, name in CURRENCIES:
        cur = krw_of(code, latest)
        old = krw_of(code, prev)
        change = ((cur - old) / old * 100) if old else 0.0
        rows.append({"code": code, "name": name, "krw": cur, "change": change})

    return {"date": latest, "rows": rows}
