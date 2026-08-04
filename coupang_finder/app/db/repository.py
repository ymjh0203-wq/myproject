import sqlite3
from datetime import datetime, timezone

from app.config.settings import DATA_DIR, DB_PATH, DEFAULT_SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coupang_product_id TEXT NOT NULL UNIQUE,
    vendor_item_id TEXT,
    catalog_id TEXT,
    product_name TEXT NOT NULL,
    core_keyword TEXT,
    product_url TEXT,
    category TEXT,
    price INTEGER,
    rating REAL,
    review_count INTEGER,
    seller_name TEXT,
    seller_id INTEGER,
    overseas_score INTEGER,
    overseas_basis TEXT,
    brand_risk_level TEXT,
    brand_risk_basis TEXT,
    hyoja_status TEXT,
    hyoja_prefix TEXT,
    option_count INTEGER,
    option_group_count INTEGER,
    option_quality TEXT,
    search_rank INTEGER,
    is_rocket INTEGER,
    search_count INTEGER,
    search_count_checked_at TEXT,
    grade TEXT,
    status TEXT NOT NULL DEFAULT '신규',
    exclusion_reason TEXT,
    discovered_at TEXT NOT NULL,
    last_checked_at TEXT,
    FOREIGN KEY (seller_id) REFERENCES sellers(id)
);

CREATE TABLE IF NOT EXISTS sellers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_name TEXT NOT NULL UNIQUE,
    first_seen_at TEXT,
    product_sample_count INTEGER DEFAULT 0,
    known_prefix_json TEXT
);

CREATE TABLE IF NOT EXISTS prefix_dictionary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL UNIQUE,
    word_type TEXT NOT NULL,
    source TEXT,
    confidence REAL,
    seller_repeat_count INTEGER DEFAULT 0,
    cross_seller_count INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS search_count_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    search_count INTEGER NOT NULL,
    checked_at TEXT NOT NULL,
    method TEXT,
    FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS discovery_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    strategy TEXT,
    analyzed_count INTEGER DEFAULT 0,
    found_count INTEGER DEFAULT 0,
    excluded_count INTEGER DEFAULT 0,
    s_grade_count INTEGER DEFAULT 0,
    status TEXT DEFAULT '진행중',
    stop_reason TEXT
);

CREATE TABLE IF NOT EXISTS discovery_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_or_keyword TEXT NOT NULL,
    last_page INTEGER DEFAULT 0,
    last_processed_product_id TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS api_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    called_at TEXT NOT NULL,
    endpoint TEXT,
    keyword TEXT,
    result_count INTEGER
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_add_missing_columns(conn: sqlite3.Connection) -> None:
    """이미 만들어진 app.db에 새 컬럼이 추가된 경우 대비 (기존 데이터 보존)."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
    additions = {"search_rank": "INTEGER", "is_rocket": "INTEGER"}
    for column, col_type in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE products ADD COLUMN {column} {col_type}")


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _migrate_add_missing_columns(conn)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()

    from app.core.dictionaries.seed import SEED_ENTRIES

    seed_prefix_dictionary(SEED_ENTRIES)


def seed_prefix_dictionary(entries: list[tuple[str, str]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO prefix_dictionary "
            "(word, word_type, source, created_at, updated_at) VALUES (?, ?, 'seed', ?, ?)",
            [(word, word_type, now, now) for word, word_type in entries],
        )
        conn.commit()
    finally:
        conn.close()


def promote_word_to_dictionary(word: str, word_type: str = "속성어") -> None:
    """사람이 효자상품 제외를 되돌렸을 때, 그 단어를 정상 단어로 사전에 등록(학습)한다."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO prefix_dictionary (word, word_type, source, created_at, updated_at) "
            "VALUES (?, ?, 'user_override', ?, ?) "
            "ON CONFLICT(word) DO UPDATE SET word_type = excluded.word_type, "
            "source = 'user_override', updated_at = excluded.updated_at",
            (word, word_type, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def lookup_word_type(word: str) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT word_type FROM prefix_dictionary WHERE word = ?", (word,)
        ).fetchone()
        return row["word_type"] if row else None
    finally:
        conn.close()


def count_seller_prefix_repeat(seller_name: str | None, prefix: str) -> int:
    """같은 판매자의 다른 상품 중 상품명이 이 접두어로 시작하는 개수(자기 자신 제외 성격의 통계)."""
    if not seller_name:
        return 0
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM products WHERE seller_name = ? AND product_name LIKE ?",
            (seller_name, prefix + "%"),
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def get_product(product_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    finally:
        conn.close()


def start_discovery_run(strategy: str) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO discovery_runs (started_at, strategy, status) VALUES (?, ?, '진행중')",
            (datetime.now(timezone.utc).isoformat(), strategy),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_discovery_run_counts(
    run_id: int, analyzed_delta: int = 0, found_delta: int = 0, excluded_delta: int = 0, s_grade_delta: int = 0
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE discovery_runs SET analyzed_count = analyzed_count + ?, "
            "found_count = found_count + ?, excluded_count = excluded_count + ?, "
            "s_grade_count = s_grade_count + ? WHERE id = ?",
            (analyzed_delta, found_delta, excluded_delta, s_grade_delta, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def close_stale_running_discovery_runs() -> None:
    """비정상 종료(창을 그냥 닫는 등)로 '진행중'에 멈춰있는 발굴 세션을 정리한다."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE discovery_runs SET ended_at = ?, status = '중단', stop_reason = '비정상 종료(앱 재시작)' "
            "WHERE status = '진행중'",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()


def finish_discovery_run(run_id: int, status: str, stop_reason: str | None = None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE discovery_runs SET ended_at = ?, status = ?, stop_reason = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), status, stop_reason, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def log_search_count(product_id: int, search_count: int, method: str = "manual") -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO search_count_log (product_id, search_count, checked_at, method) "
            "VALUES (?, ?, ?, ?)",
            (product_id, search_count, datetime.now(timezone.utc).isoformat(), method),
        )
        conn.commit()
    finally:
        conn.close()


def update_product_filters(product_id: int, **fields) -> None:
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE products SET {columns} WHERE id = ?",
            (*fields.values(), product_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_setting(key: str, default: str | None = None) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def get_all_settings() -> dict[str, str]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def set_settings(values: dict[str, str]) -> None:
    conn = get_connection()
    try:
        conn.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            list(values.items()),
        )
        conn.commit()
    finally:
        conn.close()


def list_products(status: str | None = None, grade: str | None = None) -> list[sqlite3.Row]:
    query = "SELECT * FROM products"
    conditions = []
    params: list[str] = []
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if grade is not None:
        conditions.append("grade = ?")
        params.append(grade)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY discovered_at DESC"

    conn = get_connection()
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def list_products_awaiting_check() -> list[sqlite3.Row]:
    """검색수 확인이 필요한 상품: 아직 한 번도 안 본 '검토중' + 재검사 주기가 지난 '저장' 상품."""
    recheck_normal = int(get_setting("recheck_days_normal", "30"))
    recheck_s = int(get_setting("recheck_days_s_grade", "14"))
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT * FROM products
            WHERE status = '검토중'
               OR (
                    status = '저장' AND (
                        search_count_checked_at IS NULL
                        OR (grade = 'S등급' AND julianday('now') - julianday(search_count_checked_at) >= ?)
                        OR (grade != 'S등급' AND julianday('now') - julianday(search_count_checked_at) >= ?)
                    )
               )
            ORDER BY discovered_at DESC
            """,
            (recheck_s, recheck_normal),
        ).fetchall()
    finally:
        conn.close()


def update_product_status(product_id: int, status: str, exclusion_reason: str | None = None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE products SET status = ?, exclusion_reason = ?, last_checked_at = ? WHERE id = ?",
            (status, exclusion_reason, datetime.now(timezone.utc).isoformat(), product_id),
        )
        conn.commit()
    finally:
        conn.close()


def count_products(status: str | None = None, grade: str | None = None) -> int:
    query = "SELECT COUNT(*) FROM products"
    conditions = []
    params: list[str] = []
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if grade is not None:
        conditions.append("grade = ?")
        params.append(grade)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    conn = get_connection()
    try:
        return conn.execute(query, params).fetchone()[0]
    finally:
        conn.close()


def count_products_discovered_today() -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM products WHERE date(discovered_at) = date('now')"
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def count_api_calls_last_hour(endpoint: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM api_usage_log "
            "WHERE endpoint = ? AND called_at >= datetime('now', '-1 hour')",
            (endpoint,),
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def log_api_call(endpoint: str, keyword: str, result_count: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO api_usage_log (called_at, endpoint, keyword, result_count) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), endpoint, keyword, result_count),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_product_from_search_result(
    coupang_product_id: str,
    product_name: str,
    product_url: str,
    price: int | None,
    vendor_item_id: str | None = None,
    search_rank: int | None = None,
    is_rocket: bool | None = None,
) -> tuple[int, bool]:
    """검색 결과 1건을 products 테이블에 반영한다.

    이미 있는 상품(coupang_product_id 기준)이면 last_checked_at만 갱신하고,
    없으면 status='신규'로 새로 만든다. (내부 id, 새로 추가됐는지 여부)를 반환한다.
    search_rank/is_rocket은 파트너스 API가 이미 공식으로 주는 값으로, 검색수를
    대신하지는 못하지만 수동 확인 우선순위를 매기는 데 쓴다.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM products WHERE coupang_product_id = ?", (coupang_product_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE products SET last_checked_at = ?, price = ? WHERE id = ?",
                (now, price, existing["id"]),
            )
            conn.commit()
            return existing["id"], False

        cursor = conn.execute(
            "INSERT INTO products "
            "(coupang_product_id, vendor_item_id, product_name, product_url, price, "
            " search_rank, is_rocket, status, discovered_at, last_checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, '신규', ?, ?)",
            (
                coupang_product_id,
                vendor_item_id,
                product_name,
                product_url,
                price,
                search_rank,
                1 if is_rocket else 0 if is_rocket is not None else None,
                now,
                now,
            ),
        )
        conn.commit()
        return cursor.lastrowid, True
    finally:
        conn.close()
