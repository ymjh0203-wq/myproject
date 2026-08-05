# ==========================================================
# 마켓 계정 저장소 (repositories/market_repository.py)
# ----------------------------------------------------------
# 사용자가 등록한 마켓(상점) 계정 정보를 저장/조회/삭제하는 곳입니다.
# 쿠팡 계정을 여러 개 쓰거나(상점명이 다른 경우), 쿠팡 외 다른 마켓을
# 등록할 때 여기에 저장됩니다.
#
# 주의: login_password/api_secret_key는 SQLite 파일에 평문으로 저장됩니다
# (.env에 API 키를 평문 저장하는 것과 같은 수준의 위험입니다). 이 데이터베이스
# 파일이 외부로 유출되지 않도록 주의해야 합니다 (.gitignore에 이미 포함됨).
# ==========================================================

from datetime import datetime

from database import get_connection

PLATFORM_COUPANG = "coupang"  # 실제 연동 코드가 있는 플랫폼
PLATFORM_OTHER = "other"      # 아직 연동 코드가 없는 플랫폼 (등록만 가능)


class DuplicateMarketNameError(Exception):
    """이미 사용 중인(활성 상태인) 상점/마켓명으로 새로 등록하려고 할 때 발생합니다."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def list_market_accounts(active_only: bool = True, platform: str = None) -> list:
    """등록된 마켓 계정 목록을 돌려줍니다."""
    connection = get_connection()
    try:
        query = "SELECT * FROM market_accounts"
        conditions = []
        params = []
        if active_only:
            conditions.append("is_active = 1")
        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id"
        rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_market_account(market_account_id: int) -> dict:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM market_accounts WHERE id = ?", (market_account_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def find_by_market_name(market_name: str) -> dict:
    """같은 이름으로 이미 등록된 계정이 있는지 찾습니다 (활성/비활성 모두 포함)."""
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM market_accounts WHERE market_name = ?", (market_name,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def create_market_account(
    market_name: str,
    connection_type: str,
    platform: str = PLATFORM_OTHER,
    api_vendor_id: str = None,
    api_access_key: str = None,
    api_secret_key: str = None,
    login_id: str = None,
    login_password: str = None,
) -> int:
    """
    새 마켓 계정을 등록합니다.

    market_name은 표에 UNIQUE로 걸려있습니다. 같은 이름의 계정이 이미
    "삭제"(비활성화)되어 있으면, 새로 만들지 않고 그 계정을 되살려서 새 정보로
    덮어씁니다 (삭제했던 상점을 같은 이름으로 다시 등록하는 경우를 위함).
    같은 이름의 계정이 아직 사용 중(활성)이면 DuplicateMarketNameError를 냅니다.
    """
    existing = find_by_market_name(market_name)

    connection = get_connection()
    try:
        if existing:
            if existing["is_active"]:
                raise DuplicateMarketNameError(
                    f"'{market_name}' 이름은 이미 사용 중인 상점/마켓이 있습니다. 다른 이름을 써주세요."
                )
            connection.execute(
                """
                UPDATE market_accounts
                SET platform = ?, connection_type = ?, api_vendor_id = ?, api_access_key = ?,
                    api_secret_key = ?, login_id = ?, login_password = ?, is_active = 1, created_at = ?
                WHERE id = ?
                """,
                (
                    platform,
                    connection_type,
                    api_vendor_id,
                    api_access_key,
                    api_secret_key,
                    login_id,
                    login_password,
                    _now(),
                    existing["id"],
                ),
            )
            connection.commit()
            return existing["id"]

        cursor = connection.execute(
            """
            INSERT INTO market_accounts (
                market_name, platform, connection_type, api_vendor_id, api_access_key,
                api_secret_key, login_id, login_password, is_active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            RETURNING id
            """,
            (
                market_name,
                platform,
                connection_type,
                api_vendor_id,
                api_access_key,
                api_secret_key,
                login_id,
                login_password,
                _now(),
            ),
        )
        new_id = cursor.fetchone()["id"]
        connection.commit()
        return new_id
    finally:
        connection.close()


def set_wing_id(market_account_id: int, wing_id: str) -> None:
    """
    상점의 WING 아이디를 저장합니다.
    상품문의에 답변을 등록할 때 쿠팡이 "누가 답변했는지"(replyBy)를 필수로
    요구하는데, 그 값이 판매자 WING 아이디입니다.
    """
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE market_accounts SET wing_id = ? WHERE id = ?",
            ((wing_id or "").strip() or None, market_account_id),
        )
        connection.commit()
    finally:
        connection.close()


def find_by_api_vendor_id(api_vendor_id: str) -> dict:
    """같은 Vendor ID로 이미 등록된 계정이 있는지 찾습니다 (.env 계정 자동 등록용)."""
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM market_accounts WHERE api_vendor_id = ?", (api_vendor_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def ensure_default_coupang_account(default_name: str = "굿디얼") -> None:
    """
    .env에 있는 쿠팡 계정(COUPANG_VENDOR_ID 등)이 아직 market_accounts에
    등록되어 있지 않으면, 자동으로 하나 등록해줍니다. (여러 상점을 다루기
    전까지 .env 하나만으로 쓰던 기존 사용자를 위한 자동 이전입니다)
    이름은 나중에 설정 화면에서 바꿀 수 있습니다.
    """
    import config

    if not config.COUPANG_VENDOR_ID:
        return
    if find_by_api_vendor_id(config.COUPANG_VENDOR_ID):
        return

    market_name = default_name
    connection = get_connection()
    try:
        # 이름이 이미 다른 계정에서 쓰이고 있으면 숫자를 붙여 겹치지 않게 합니다.
        suffix = 2
        while connection.execute(
            "SELECT 1 FROM market_accounts WHERE market_name = ?", (market_name,)
        ).fetchone():
            market_name = f"{default_name}{suffix}"
            suffix += 1
    finally:
        connection.close()

    create_market_account(
        market_name=market_name,
        connection_type="api",
        platform=PLATFORM_COUPANG,
        api_vendor_id=config.COUPANG_VENDOR_ID,
        api_access_key=config.COUPANG_ACCESS_KEY,
        api_secret_key=config.COUPANG_SECRET_KEY,
    )


def deactivate_market_account(market_account_id: int) -> None:
    """마켓 계정을 삭제하지 않고 비활성화(사용 안 함) 처리합니다."""
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE market_accounts SET is_active = 0 WHERE id = ?", (market_account_id,)
        )
        connection.commit()
    finally:
        connection.close()
