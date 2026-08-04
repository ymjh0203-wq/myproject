# ==========================================================
# 설정값 저장소 (repositories/settings_repository.py)
# ----------------------------------------------------------
# app_settings 테이블은 "키(key) - 값(value)" 형태로 아무 설정값이나
# 저장할 수 있는 간단한 표입니다. 예를 들어 "마지막 주문 수집 시각" 같은
# 값을 저장해둘 때 씁니다.
# ==========================================================

from database import get_connection


def get_setting(key: str, default: str = None) -> str:
    """설정값을 하나 읽어옵니다. 없으면 default를 돌려줍니다."""
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default
    finally:
        connection.close()


def set_setting(key: str, value: str) -> None:
    """설정값을 저장합니다. 이미 있으면 덮어씁니다."""
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        connection.commit()
    finally:
        connection.close()
