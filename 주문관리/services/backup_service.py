# ==========================================================
# 자동 DB 백업 (services/backup_service.py)
# ----------------------------------------------------------
# 클라우드(Supabase/PostgreSQL)에 있는 전체 데이터를 하루 한 번 로컬 SQLite
# 파일로 통째로 복사해 둡니다. 사고(실수 삭제·이전 실패 등)가 나도 이 파일로
# 되살릴 수 있습니다. 로컬 SQLite 모드로 쓸 때도 그대로 백업됩니다.
#   - 저장 위치: <프로젝트>/backups/order_management_backup_YYYYMMDD_HHMMSS.db
#   - 하루 1회만(앱 첫 실행 시 백그라운드). 같은 날 이미 했으면 건너뜀.
#   - 최근 N개(_KEEP)만 남기고 오래된 백업은 자동 삭제.
#   - 직접 열어볼 수 있는 SQLite 파일이라, 급하면 DB_PATH로 지정해 바로 쓸 수 있습니다.
# ==========================================================

import os
import sqlite3
import threading
from datetime import date, datetime

import config
import database
from repositories import settings_repository

# 백업할 표 목록 (migrate_to_supabase.py와 동일한 전체 집합).
_TABLES = [
    "market_accounts", "orders", "order_items", "shipping_information",
    "customs_validations", "order_status_history", "api_sync_history",
    "app_settings", "product_link_cache", "claims", "exchange_requests",
    "product_inquiries", "call_center_inquiries", "order_cost",
    "sms_send_log", "taobao_link", "return_compensation", "excel_templates",
]

_KEEP = 14                       # 보관할 백업 개수(최근 것부터)
_LAST_DATE_KEY = "last_auto_backup_date"
_backup_lock = threading.Lock()  # 동시에 두 번 돌지 않게


def backups_dir() -> str:
    path = os.path.join(config.BASE_DIR, "backups")
    os.makedirs(path, exist_ok=True)
    return path


def list_backups() -> list:
    """저장된 백업 파일을 최신순으로 [{name, path, size, mtime}] 로 돌려줍니다."""
    d = backups_dir()
    items = []
    for name in os.listdir(d):
        if name.startswith("order_management_backup_") and name.endswith(".db"):
            p = os.path.join(d, name)
            try:
                stat = os.stat(p)
                items.append({"name": name, "path": p, "size": stat.st_size,
                              "mtime": datetime.fromtimestamp(stat.st_mtime)})
            except OSError:
                pass
    return sorted(items, key=lambda x: x["mtime"], reverse=True)


def _table_columns(connection, table: str) -> list:
    """표의 컬럼 이름 목록. (첫 행이 있으면 그 키, 없으면 스키마에서 조회)"""
    row = connection.execute(f'SELECT * FROM "{table}" LIMIT 1').fetchone()
    if row is not None:
        return list(row.keys())
    # 빈 표: 스키마에서 컬럼명을 가져옵니다(Postgres/SQLite 모두 대응).
    if config.use_postgres():
        cols = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position", (table,)
        ).fetchall()
        return [c["column_name"] for c in cols]
    cols = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [c["name"] for c in cols]


def _do_backup() -> str:
    """실제 백업 1회. 돌려주는 값: 생성된 백업 파일 경로."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backups_dir(), f"order_management_backup_{ts}.db")
    src = database.get_connection()
    dst = sqlite3.connect(dest)
    try:
        for table in _TABLES:
            try:
                cols = _table_columns(src, table)
            except Exception:  # noqa: BLE001 (없는 표는 건너뜀)
                continue
            if not cols:
                continue
            col_defs = ", ".join(f'"{c}"' for c in cols)
            dst.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({col_defs})')
            rows = src.execute(f'SELECT * FROM "{table}"').fetchall()
            if rows:
                placeholders = ", ".join("?" for _ in cols)
                data = [tuple(r[c] for c in cols) for r in rows]
                dst.executemany(
                    f'INSERT INTO "{table}" ({col_defs}) VALUES ({placeholders})', data
                )
        dst.commit()
    finally:
        dst.close()
        src.close()
    _rotate()
    return dest


def _rotate() -> None:
    """최근 _KEEP개만 남기고 오래된 백업 파일 삭제."""
    for item in list_backups()[_KEEP:]:
        try:
            os.remove(item["path"])
        except OSError:
            pass


def run_backup_now() -> str:
    """수동 백업(지금 바로). 생성된 파일 경로를 돌려줍니다."""
    with _backup_lock:
        path = _do_backup()
        settings_repository.set_setting(_LAST_DATE_KEY, date.today().isoformat())
        return path


def last_backup_date() -> str:
    return settings_repository.get_setting(_LAST_DATE_KEY, "") or ""


def _backup_thread() -> None:
    try:
        with _backup_lock:
            if last_backup_date() == date.today().isoformat():
                return  # 오늘 이미 함
            _do_backup()
            settings_repository.set_setting(_LAST_DATE_KEY, date.today().isoformat())
    except Exception:  # noqa: BLE001 (백업 실패가 앱을 막으면 안 됨)
        pass


def run_daily_backup_async() -> None:
    """앱 첫 실행 때 호출. 오늘 백업이 없으면 백그라운드로 1회 백업합니다(화면 안 막음)."""
    if last_backup_date() == date.today().isoformat():
        return
    threading.Thread(target=_backup_thread, daemon=True).start()
