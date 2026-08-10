# ============================================================
# apply_migration.py
# migrations/ 폴더의 .sql 파일들을 Supabase(PostgreSQL)에 순서대로 적용합니다.
# ------------------------------------------------------------
# 준비:
#   1) pip install -r requirements.txt
#   2) .env 파일에 DATABASE_URL 을 넣어주세요.
#      (Supabase 대시보드 → Settings → Database → Connection string(URI))
#
# 실행 (상품등록 폴더 안에서):
#   python apply_migration.py            # migrations/*.sql 전부 적용
#   python apply_migration.py 001_init   # 특정 파일만 적용(확장자 생략 가능)
#
# * 마이그레이션 SQL 은 IF NOT EXISTS 위주라 여러 번 돌려도 안전합니다.
# * 각 파일은 하나의 트랜잭션으로 실행됩니다(중간 실패 시 그 파일은 롤백).
# ============================================================

import glob
import os
import sys

import psycopg  # psycopg 3
from dotenv import load_dotenv

# 이 스크립트가 있는 폴더 기준 경로
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS_DIR = os.path.join(BASE_DIR, "migrations")


def get_database_url() -> str:
    """.env 에서 Supabase 연결 문자열을 읽어옵니다."""
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    url = os.getenv("DATABASE_URL")
    if not url:
        print("[중단] .env 에 DATABASE_URL 이 없습니다.")
        print("       Supabase 대시보드 → Settings → Database → Connection string(URI)")
        print("       값을 .env 의 DATABASE_URL= 뒤에 붙여넣고 다시 실행해주세요.")
        sys.exit(1)
    return url


def find_sql_files(only: str | None) -> list[str]:
    """적용할 .sql 파일 목록을 이름순으로 돌려줍니다."""
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))
    if only:
        # 확장자를 붙이지 않았어도 매칭되게 함 (예: "001_init")
        stem = only[:-4] if only.endswith(".sql") else only
        files = [f for f in files if os.path.basename(f).startswith(stem)]
        if not files:
            print(f"[중단] '{only}' 에 해당하는 마이그레이션 파일을 찾지 못했습니다.")
            sys.exit(1)
    return files


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    url = get_database_url()
    files = find_sql_files(only)

    print(f"[시작] 적용할 파일 {len(files)}개:")
    for f in files:
        print(f"   - {os.path.basename(f)}")

    # autocommit=False: 파일 하나를 한 트랜잭션으로 실행(실패 시 그 파일만 롤백)
    with psycopg.connect(url) as conn:
        for f in files:
            name = os.path.basename(f)
            with open(f, "r", encoding="utf-8") as fh:
                sql = fh.read()
            print(f"[적용] {name} ...", end=" ")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
                print("완료")
            except Exception as e:
                conn.rollback()
                print("실패")
                print(f"   ! 오류: {e}")
                sys.exit(1)

    print("[끝] 모든 마이그레이션을 적용했습니다.")


if __name__ == "__main__":
    main()
