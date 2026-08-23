# ============================================================
# db.py  —  데이터 저장소 연결/저장
# ------------------------------------------------------------
# 두 가지 백엔드를 자동으로 고릅니다:
#   - .env 에 DATABASE_URL 있으면  → Supabase(PostgreSQL)
#   - 없으면                        → 로컬 SQLite (data/sangpum.db)  ← 기본
# 개인용이면 SQLite로 아무 설정 없이 바로 저장됩니다. 나중에 DATABASE_URL 만
# 넣으면 클라우드로 전환됩니다.
# ============================================================

import json
import os
import sqlite3

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SQLITE_PATH = os.path.join(DATA_DIR, "sangpum.db")

REQUIRED_TABLES = [
    "products_raw", "products_processed", "keywords",
    "category_mapping", "listings", "benchmark_seeds",
]

# 로컬 SQLite 스키마 (Postgres 마이그레이션 001+002 과 같은 표들)
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmark_seeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_type TEXT NOT NULL,
    seed_ref TEXT NOT NULL,
    seed_image_url TEXT,
    seed_image_path TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS products_raw (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_platform TEXT NOT NULL DEFAULT 'taobao',
    source_url TEXT NOT NULL UNIQUE,
    source_item_id TEXT,
    title_original TEXT,
    price_original REAL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    options TEXT,
    image_urls TEXT,
    sales_count INTEGER,
    review_count INTEGER,
    shop_name TEXT,
    raw_payload TEXT,
    seed_id INTEGER REFERENCES benchmark_seeds(id),
    match_rank INTEGER,
    collected_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS products_processed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id INTEGER NOT NULL REFERENCES products_raw(id),
    title_ko TEXT, description_ko TEXT, options_ko TEXT,
    weight_g REAL, shipping_cost_krw REAL, cost_price_krw REAL,
    sale_price_krw REAL, margin_rate REAL,
    main_image_url TEXT, detail_images TEXT,
    category_smartstore TEXT, category_esm TEXT,
    process_status TEXT NOT NULL DEFAULT '가공중',
    processed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marketplace TEXT NOT NULL, keyword TEXT NOT NULL,
    rank INTEGER, search_volume INTEGER,
    collected_date TEXT NOT NULL DEFAULT (date('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS category_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    taobao_category_path TEXT NOT NULL, marketplace TEXT NOT NULL,
    market_category_id TEXT, market_category_path TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    processed_id INTEGER NOT NULL REFERENCES products_processed(id),
    marketplace TEXT NOT NULL,
    list_status TEXT NOT NULL DEFAULT '등록대기',
    market_product_id TEXT, listed_at TEXT, fail_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def database_url() -> str | None:
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    url = os.getenv("DATABASE_URL")
    return url.strip() if url and url.strip() else None


def is_sqlite() -> bool:
    return database_url() is None


def backend_name() -> str:
    return "로컬 SQLite" if is_sqlite() else "Supabase(PostgreSQL)"


def get_conn():
    """백엔드에 맞는 DB 연결을 돌려줍니다(SQLite면 표도 자동 생성)."""
    url = database_url()
    if url:
        import psycopg
        return psycopg.connect(url)
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.executescript(SQLITE_SCHEMA)
    conn.commit()
    return conn


def _json(v):
    return json.dumps(v, ensure_ascii=False) if v is not None else None


# ------------------------------------------------------------
# 저장 함수
# ------------------------------------------------------------
def upsert_product_raw(conn, data: dict) -> int:
    """수집 상품을 products_raw 에 저장(같은 source_url 이면 갱신). 반환: id"""
    if is_sqlite():
        sql = """
            INSERT INTO products_raw
                (source_platform,source_url,source_item_id,title_original,price_original,
                 currency,options,image_urls,sales_count,review_count,shop_name,raw_payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_url) DO UPDATE SET
                source_item_id=excluded.source_item_id, title_original=excluded.title_original,
                price_original=excluded.price_original, currency=excluded.currency,
                options=excluded.options, image_urls=excluded.image_urls,
                sales_count=excluded.sales_count, review_count=excluded.review_count,
                shop_name=excluded.shop_name, raw_payload=excluded.raw_payload,
                collected_at=datetime('now')
        """
        params = [
            data.get("source_platform", "taobao"), data["source_url"], data.get("source_item_id"),
            data.get("title_original"), data.get("price_original"), data.get("currency", "CNY"),
            _json(data.get("options")), _json(data.get("image_urls")),
            data.get("sales_count"), data.get("review_count"), data.get("shop_name"),
            _json(data.get("raw_payload")),
        ]
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        cur.execute("SELECT id FROM products_raw WHERE source_url=?", (data["source_url"],))
        return cur.fetchone()[0]

    from psycopg.types.json import Json
    sql = """
        INSERT INTO products_raw
            (source_platform, source_url, source_item_id, title_original, price_original,
             currency, options, image_urls, sales_count, review_count, shop_name, raw_payload)
        VALUES
            (%(source_platform)s, %(source_url)s, %(source_item_id)s, %(title_original)s,
             %(price_original)s, %(currency)s, %(options)s, %(image_urls)s,
             %(sales_count)s, %(review_count)s, %(shop_name)s, %(raw_payload)s)
        ON CONFLICT (source_url) DO UPDATE SET
            source_item_id = EXCLUDED.source_item_id, title_original = EXCLUDED.title_original,
            price_original = EXCLUDED.price_original, currency = EXCLUDED.currency,
            options = EXCLUDED.options, image_urls = EXCLUDED.image_urls,
            sales_count = EXCLUDED.sales_count, review_count = EXCLUDED.review_count,
            shop_name = EXCLUDED.shop_name, raw_payload = EXCLUDED.raw_payload,
            collected_at = now()
        RETURNING id;
    """
    params = {
        "source_platform": data.get("source_platform", "taobao"),
        "source_url": data["source_url"], "source_item_id": data.get("source_item_id"),
        "title_original": data.get("title_original"), "price_original": data.get("price_original"),
        "currency": data.get("currency", "CNY"),
        "options": Json(data["options"]) if data.get("options") is not None else None,
        "image_urls": Json(data["image_urls"]) if data.get("image_urls") is not None else None,
        "sales_count": data.get("sales_count"), "review_count": data.get("review_count"),
        "shop_name": data.get("shop_name"),
        "raw_payload": Json(data["raw_payload"]) if data.get("raw_payload") is not None else None,
    }
    with conn.cursor() as cur:
        cur.execute(sql, params)
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def insert_benchmark_seed(conn, seed: dict) -> int:
    """벤치마킹 씨앗을 benchmark_seeds 에 저장하고 id 반환."""
    cols = ("seed_type", "seed_ref", "seed_image_url", "seed_image_path", "note")
    vals = [seed.get(c) for c in cols]
    if is_sqlite():
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO benchmark_seeds ({','.join(cols)}) VALUES (?,?,?,?,?)", vals
        )
        conn.commit()
        return cur.lastrowid
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO benchmark_seeds ({','.join(cols)}) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            vals,
        )
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def link_to_seed(conn, product_id: int, seed_id: int, match_rank: int | None = None) -> None:
    """products_raw 행을 씨앗/순위와 연결."""
    ph = "?" if is_sqlite() else "%s"
    cur = conn.cursor()
    cur.execute(
        f"UPDATE products_raw SET seed_id={ph}, match_rank={ph} WHERE id={ph}",
        (seed_id, match_rank, product_id),
    )
    conn.commit()
    if not is_sqlite():
        cur.close()


# ------------------------------------------------------------
# 화면용 조회 함수 (백엔드 차이를 여기서 흡수)
# ------------------------------------------------------------
def _today_filter(col: str) -> str:
    return f"date({col}) = date('now')" if is_sqlite() else f"{col}::date = current_date"


def counts() -> dict:
    """홈 대시보드용 집계."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM products_raw")
        raw_total = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) FROM products_raw WHERE {_today_filter('collected_at')}")
        raw_today = cur.fetchone()[0]
        cur.execute("SELECT marketplace, count(*) FROM listings GROUP BY marketplace")
        upload_total = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute(
            f"SELECT marketplace, count(*) FROM listings "
            f"WHERE {_today_filter('created_at')} GROUP BY marketplace"
        )
        upload_today = {r[0]: r[1] for r in cur.fetchall()}
        return {"raw_total": raw_total, "raw_today": raw_today,
                "upload_total": upload_total, "upload_today": upload_today}
    finally:
        conn.close()


def fetch_products(limit: int = 300):
    """등록 상품 목록용. (columns, rows) 를 돌려줍니다."""
    cols = ["id", "title_original", "price_original", "currency",
            "sales_count", "shop_name", "seed_id", "match_rank", "collected_at"]
    conn = get_conn()
    try:
        cur = conn.cursor()
        ph = "?" if is_sqlite() else "%s"
        cur.execute(
            f"SELECT {','.join(cols)} FROM products_raw ORDER BY id DESC LIMIT {ph}",
            (limit,),
        )
        rows = cur.fetchall()
        return cols, [list(r) for r in rows]
    finally:
        conn.close()


def db_status() -> dict:
    """설정 '시스템' 탭용 연결/표 상태."""
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            if is_sqlite():
                cur.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN "
                    "('products_raw','products_processed','keywords','category_mapping',"
                    "'listings','benchmark_seeds')"
                )
            else:
                cur.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name IN "
                    "('products_raw','products_processed','keywords','category_mapping',"
                    "'listings','benchmark_seeds')"
                )
            present = cur.fetchone()[0]
        finally:
            conn.close()
        return {"ok": True, "backend": backend_name(),
                "tables_present": present, "tables_total": len(REQUIRED_TABLES)}
    except Exception as e:
        return {"ok": False, "backend": backend_name(), "error": str(e)}
