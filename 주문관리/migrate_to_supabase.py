# ==========================================================
# 기존 SQLite 데이터를 Supabase(PostgreSQL)로 옮기는 1회용 스크립트
# ----------------------------------------------------------
# 하는 일:
#   1) .env의 DATABASE_URL(Supabase 연결 문자열)이 있는지 확인
#   2) Supabase에 필요한 표(테이블)들을 만든다 (database.init_db)
#   3) 로컬 order_management.db 의 모든 데이터를 Supabase로 그대로 복사
#      (id 값까지 그대로 유지해서 주문-주문상품 연결이 안 깨지게 함)
#   4) 자동증가(id) 시퀀스를 마지막 값 다음으로 맞춰준다
#
# 실행 방법 (주문관리 폴더 안에서):
#   python migrate_to_supabase.py            # 안전 모드: 대상 표에 데이터가
#                                            #   이미 있으면 멈춤(덮어쓰기 방지)
#   python migrate_to_supabase.py --reset    # 대상 표를 먼저 싹 비우고 새로 복사
#
# * 이 스크립트는 로컬 SQLite는 읽기만 합니다. 원본은 절대 건드리지 않습니다.
# ==========================================================

import sqlite3
import sys

import config
import database

# 외래키(FK) 때문에 "먼저 채워야 하는 표"부터 순서대로 나열합니다.
# (예: order_items 는 orders.id 를 참조하므로 orders 를 먼저 넣어야 함)
# ★모든 데이터 표를 빠짐없이 포함해야 합니다(하나라도 빠지면 그 표 데이터가 클라우드로
#   안 옮겨져 '사라진 것처럼' 보입니다 — 실제로 taobao_link 등이 빠져 사고났음).
#   FK 있는 표(order_cost·sms_send_log→orders)는 orders 뒤에 둡니다.
TABLE_ORDER = [
    "market_accounts",
    "orders",
    "order_items",
    "shipping_information",
    "customs_validations",
    "order_status_history",
    "api_sync_history",
    "app_settings",
    "product_link_cache",
    "claims",
    "exchange_requests",
    "product_inquiries",
    "call_center_inquiries",
    "order_cost",           # 매출정리 원가·배송비 (order_id PK, FK→orders)
    "sms_send_log",         # 문자 발송 기록 (order_id FK→orders)
    "taobao_link",          # 상품별 타오바오 링크
    "return_compensation",  # 반품 보상관리 상태
    "excel_templates",      # 엑셀 양식
]

# id 컬럼이 자동증가(SERIAL)인 표들. 복사 후 시퀀스를 다시 맞춰줘야 합니다.
# (자동증가 id가 없는 표는 제외: app_settings=key, product_link_cache=vendor_item_id,
#  return_compensation=receipt_id, order_cost=order_id)
SERIAL_ID_TABLES = [
    t for t in TABLE_ORDER
    if t not in ("app_settings", "product_link_cache", "return_compensation", "order_cost")
]


def _sqlite_columns(sqlite_conn, table_name):
    """SQLite 표의 컬럼 이름 목록을 순서대로 돌려줍니다."""
    return [row[1] for row in sqlite_conn.execute(f"PRAGMA table_info({table_name})")]


def _postgres_columns(pg_conn, table_name):
    """PostgreSQL 표에 실제로 있는 컬럼 이름들(집합)을 돌려줍니다."""
    rows = pg_conn.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ?
        """,
        (table_name,),
    ).fetchall()
    return {row["column_name"] for row in rows}


def _target_row_count(pg_conn, table_name):
    row = pg_conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()
    return row["c"]


def main():
    reset = "--reset" in sys.argv[1:]

    if not config.use_postgres():
        print("[중단] .env 에 DATABASE_URL(Supabase 연결 문자열)이 없습니다.")
        print("       Supabase 대시보드 → Settings → Database → Connection string(URI)")
        print("       값을 .env 의 DATABASE_URL= 뒤에 붙여넣고 다시 실행해주세요.")
        sys.exit(1)

    import os

    if not os.path.exists(config.DB_PATH):
        print(f"[중단] 옮길 로컬 데이터베이스 파일이 없습니다: {config.DB_PATH}")
        sys.exit(1)

    print("[1/4] Supabase에 표(테이블) 준비 중...")
    database.init_db()  # DATABASE_URL이 있으므로 PostgreSQL 쪽에 표를 만듭니다.

    # 원본 SQLite (읽기 전용으로 접근)
    sqlite_conn = sqlite3.connect(config.DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    pg_conn = database.get_connection()  # PostgreSQL 연결(래퍼)
    try:
        # 안전장치: 대상 표에 이미 데이터가 있으면 기본적으로 멈춥니다.
        if not reset:
            non_empty = [t for t in TABLE_ORDER if _target_row_count(pg_conn, t) > 0]
            if non_empty:
                print("[중단] Supabase 쪽에 이미 데이터가 있는 표가 있습니다:")
                print("       " + ", ".join(non_empty))
                print("       실수로 덮어쓰지 않도록 멈췄습니다.")
                print("       정말 새로 옮기려면:  python migrate_to_supabase.py --reset")
                sys.exit(1)
        else:
            print("[reset] 대상 표들을 먼저 비웁니다...")
            # CASCADE + RESTART IDENTITY: FK 상관없이 싹 비우고 id도 1부터 리셋
            pg_conn.execute(
                "TRUNCATE TABLE "
                + ", ".join(TABLE_ORDER)
                + " RESTART IDENTITY CASCADE"
            )
            pg_conn.commit()

        print("[2/4] 데이터 복사 중...")
        total = 0
        for table in TABLE_ORDER:
            sqlite_cols = _sqlite_columns(sqlite_conn, table)
            if not sqlite_cols:
                print(f"   - {table}: (원본에 표 없음, 건너뜀)")
                continue
            pg_cols = _postgres_columns(pg_conn, table)
            # 양쪽에 모두 있는 컬럼만 복사합니다(스키마 차이로 인한 오류 방지).
            cols = [c for c in sqlite_cols if c in pg_cols]
            missing = [c for c in sqlite_cols if c not in pg_cols]
            if missing:
                print(f"   ! {table}: Supabase에 없는 컬럼은 건너뜀 -> {missing}")

            rows = sqlite_conn.execute(
                f"SELECT {', '.join(cols)} FROM {table}"
            ).fetchall()
            if not rows:
                print(f"   - {table}: 0건")
                continue

            # ★한 줄씩 넣으면 원격(Supabase) 왕복이 너무 많아 아주 느립니다.
            #   execute_values 로 '한 번에 여러 건' 넣어 수천 배 빠르게 복사합니다.
            from psycopg2.extras import execute_values
            insert_sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s"
            data = [tuple(row[c] for c in cols) for row in rows]
            cur = pg_conn.cursor()
            execute_values(cur, insert_sql, data, page_size=1000)
            pg_conn.commit()
            total += len(rows)
            print(f"   - {table}: {len(rows)}건", flush=True)

        print("[3/4] 자동증가(id) 번호 맞추는 중...")
        for table in SERIAL_ID_TABLES:
            # 방금 넣은 데이터의 가장 큰 id 다음부터 새 id가 나오도록 시퀀스를 맞춥니다.
            pg_conn.execute(
                """
                SELECT setval(
                    pg_get_serial_sequence(?, 'id'),
                    (SELECT COALESCE(MAX(id), 1) FROM """ + table + """),
                    (SELECT COUNT(*) > 0 FROM """ + table + """)
                )
                """,
                (table,),
            )
        pg_conn.commit()

        print(f"[4/4] 완료! 총 {total}건을 Supabase로 옮겼습니다.")
    finally:
        pg_conn.close()
        sqlite_conn.close()


if __name__ == "__main__":
    main()
