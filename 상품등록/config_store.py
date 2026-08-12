# ============================================================
# config_store.py  —  설정/키워드 규칙을 로컬 JSON 에 저장/불러오기
# ------------------------------------------------------------
# DB 연결 전에도 바로 쓰도록 개인 설정값은 data/ 폴더의 JSON 에 보관합니다.
#   data/config.json      : 가격/마진/수수료/배송비 등 필수 설정
#   data/word_rules.json  : 키워드 프리셋 / 경고 단어 / 단어 치환
# (나중에 여러 기기에서 쓰게 되면 DB 표로 옮길 수 있습니다.)
# ============================================================

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
WORDS_PATH = os.path.join(DATA_DIR, "word_rules.json")

# 우리 등록 대상 마켓 (브리핑 범위: 스마트스토어 + 옥션/지마켓 ESM)
MARKET_KEYS = {"smartstore": "스마트스토어", "esm": "옥션/G마켓(ESM)"}

DEFAULT_CONFIG = {
    "margin_percent": 30,          # 퍼센트 마진(%)
    "margin_add": 0,               # 더하기 마진(원)
    "overseas_shipping": 0,        # 해외 배송비(원)
    "card_fee": 0.0,               # 카드 수수료(%)
    "market_fees": {"smartstore": 0.0, "esm": 0.0},  # 마켓별 판매 수수료(%)
    "market_discount": 0,          # 마켓 표기 할인율(%)
    "round_unit": 100,             # 단위 올림(원)
    "customs": "부과 대상 아님",     # 관부가세 설정
    "option_price_basis": "최저 옵션가 기준",  # 옵션 대표 가격 기준
    "shipping_type": "무료 배송",    # 배송비 종류
    "return_fee": 0,               # 반품비(원)
    "exchange_fee": 0,             # 교환비(원)
}

DEFAULT_WORDS = {
    "presets": [],   # [{"name": str, "keywords": [str, ...]}]
    "warn_words": [],  # [{"word": str, "note": str}]
    "replaces": [],  # [{"from": str, "to": str}]
}


def _ensure_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load(path: str, default: dict) -> dict:
    if not os.path.exists(path):
        return dict(default)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 기본값에 없는 키가 빠져 있으면 채워줌(버전 호환)
        merged = dict(default)
        merged.update(data)
        return merged
    except Exception:
        return dict(default)


def _save(path: str, data: dict) -> None:
    _ensure_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config() -> dict:
    return _load(CONFIG_PATH, DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    _save(CONFIG_PATH, cfg)


def load_words() -> dict:
    return _load(WORDS_PATH, DEFAULT_WORDS)


def save_words(words: dict) -> None:
    _save(WORDS_PATH, words)
