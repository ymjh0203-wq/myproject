# ==========================================================
# 데이터베이스 연결 및 초기화 (database.py)
# ----------------------------------------------------------
# SQLite는 별도 설치 없이 파이썬에 기본 포함된 "파일 하나짜리" 데이터베이스입니다.
# config.DB_PATH 에 지정된 파일(기본값: order_management.db)에 모든 데이터가 저장됩니다.
#
# 이 파일은 "연결을 얻는 함수"와 "테이블을 만드는 함수"만 담당합니다.
# 실제로 데이터를 넣고 빼는 코드는 repositories/ 폴더에서 담당합니다.
# ==========================================================

import sqlite3

import config


def get_connection() -> sqlite3.Connection:
    """데이터베이스 파일에 연결합니다. 파일이 없으면 자동으로 새로 만들어집니다."""
    # timeout: 여러 수집 작업을 동시에(병렬로) 돌릴 때, 한 작업이 DB에 쓰는 동안
    # 다른 작업이 잠깐 기다렸다가 이어서 쓰도록 합니다 (안 그러면 "database is
    # locked" 오류가 납니다). 홈 화면의 "전체 한번에 새로고침"이 병렬로 돌기 때문에 필요합니다.
    connection = sqlite3.connect(config.DB_PATH, timeout=30)
    # 외래키(FK) 제약을 지키도록 설정합니다 (예: 존재하지 않는 order_id 참조 방지).
    connection.execute("PRAGMA foreign_keys = ON")
    # 조회 결과를 딕셔너리처럼 컬럼명으로 접근할 수 있게 해줍니다.
    connection.row_factory = sqlite3.Row
    return connection


# ----------------------------------------------------------
# 테이블 생성 SQL 모음
# ----------------------------------------------------------
CREATE_TABLE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_name TEXT NOT NULL,           -- 마켓명 (예: 'coupang')
        market_order_id TEXT NOT NULL,       -- 마켓의 주문번호
        shipment_box_id TEXT,                -- 배송번호(묶음배송 단위 식별값)
        ordered_at TEXT,                     -- 주문일시
        market_status TEXT,                  -- 마켓 원본 상태
        work_status TEXT NOT NULL,           -- 내부 작업 상태 (models.py 참고)
        first_collected_at TEXT NOT NULL,    -- 최초 수집일시
        last_updated_at TEXT NOT NULL,       -- 마지막 갱신일시
        raw_response_json TEXT,              -- 원본 API 응답 중 필요한 최소 데이터
        shipping_fee INTEGER,                -- 배송비
        settlement_amount INTEGER,           -- 정산금 (실제 정산받는 금액)
        delivery_company_code TEXT,          -- 택배사 코드 (models.COURIER_CODES 참고, 우리가 등록한 값)
        invoice_number TEXT,                 -- 송장번호 (우리가 등록한 값)
        shipped_at TEXT,                     -- 송장 등록(발송 처리)한 일시
        market_account_id INTEGER REFERENCES market_accounts(id),  -- 어느 상점/계정의 주문인지
        orderer_name TEXT,                   -- 구매자 이름 (쿠팡 orderer.name)
        orderer_phone TEXT,                  -- 구매자 전화번호 (쿠팡 orderer.safeNumber)
        paid_at TEXT,                        -- 결제일시 (쿠팡 paidAt)
        remote_area INTEGER,                 -- 도서산간 여부 (쿠팡 remoteArea, 0 또는 1)
        market_delivery_company_name TEXT,   -- 쿠팡이 알려주는 택배사명 (우리가 등록 안 해도 쿠팡 Wing에서 직접 처리된 경우 참고용)
        market_invoice_number TEXT,          -- 쿠팡이 알려주는 송장번호 (위와 같은 이유)
        quickstar_order_no TEXT,             -- 퀵스타 배대지 신청번호 (접수 성공 시 받은 그룹번호)
        quickstar_invoice TEXT,              -- 퀵스타가 알려준 운송장번호
        quickstar_submitted_at TEXT,         -- 퀵스타에 접수한 일시
        cs_memo TEXT,                        -- 사용자가 직접 적는 고객 특이사항 메모
        UNIQUE (market_name, market_order_id, shipment_box_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        market_item_id TEXT,                 -- 상품주문 단위 식별값
        product_name TEXT,
        option_name TEXT,
        quantity INTEGER,
        sales_amount INTEGER,
        seller_product_code TEXT,            -- 판매자상품코드 (쿠팡 orderItems[].sellerProductId)
        seller_option_code TEXT,             -- 판매자옵션코드 (쿠팡 orderItems[].externalVendorSkuCode)
        delivery_charge_type_name TEXT,      -- 배송구분 (쿠팡 orderItems[].deliveryChargeTypeName)
        estimated_shipping_date TEXT         -- 발송예정일 (쿠팡 orderItems[].estimatedShippingDate)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shipping_information (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
        receiver_name TEXT,
        receiver_phone_raw TEXT,             -- 마켓이 준 원본 전화번호(안심번호일 수 있음)
        customs_phone TEXT,                  -- 실제 통관 검증에 사용한 전화번호
        pccc TEXT,                            -- 개인통관고유부호
        zip_code TEXT,
        address_basic TEXT,
        address_detail TEXT,
        validation_status TEXT,              -- models.py의 VALIDATION_STATUS_* 값
        last_validated_at TEXT,
        validation_message TEXT,
        validation_snapshot_hash TEXT,       -- 검증 당시 4개 값의 지문값 (재검증 필요 감지용)
        name_check_message TEXT,             -- 이름 항목만의 검사 결과 메시지 (문제 없으면 NULL)
        phone_check_message TEXT,            -- 전화번호 항목만의 검사 결과 메시지
        pccc_check_message TEXT,             -- 개인통관고유부호 항목만의 검사 결과 메시지
        zip_check_message TEXT               -- 우편번호 항목만의 검사 결과 메시지
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS customs_validations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        validated_at TEXT NOT NULL,
        validator_type TEXT NOT NULL,        -- LocalFormatValidator / MockCustomsValidator / UnipassCustomsValidator
        is_success INTEGER NOT NULL,         -- 0 또는 1
        status_code TEXT,
        message TEXT,
        retryable INTEGER                    -- 0 또는 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_status_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        changed_at TEXT NOT NULL,
        old_status TEXT,
        new_status TEXT NOT NULL,
        changed_by TEXT                      -- 'manual' 또는 'auto' 등
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_sync_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        synced_at TEXT NOT NULL,
        mode TEXT NOT NULL,                  -- 'mock' 또는 'real'
        period_from TEXT,
        period_to TEXT,
        fetched_count INTEGER DEFAULT 0,
        new_count INTEGER DEFAULT 0,
        updated_count INTEGER DEFAULT 0,
        error_count INTEGER DEFAULT 0,
        status TEXT,                         -- 'success' / 'partial' / 'fail'
        error_message TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    """
    -- 상품 페이지 링크 캐시.
    -- 쿠팡 주문 데이터에는 상품 페이지 주소(productId)가 없고 vendorItemId만
    -- 있습니다. 올바른 상품 URL을 만들려면 productId/itemId가 필요해서, 상품조회
    -- API로 한 번 알아낸 값을 여기에 저장해 둡니다. (주문상품 표는 재수집 때마다
    -- 지워지므로, 여기 따로 보관해야 재수집 후에도 링크가 유지됩니다)
    CREATE TABLE IF NOT EXISTS product_link_cache (
        vendor_item_id TEXT PRIMARY KEY,     -- 주문상품 식별값 (order_items.market_item_id)
        product_id TEXT,                     -- 쿠팡 노출상품ID (URL 경로에 들어감)
        item_id TEXT,                        -- 쿠팡 아이템ID (URL 파라미터)
        resolved_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_name TEXT NOT NULL UNIQUE,    -- 사용자가 정한 상점/계정 이름 (예: '굿디얼')
        platform TEXT NOT NULL DEFAULT 'coupang',  -- 실제 연동 코드가 어느 플랫폼용인지 ('coupang'만 실제 동작함)
        connection_type TEXT NOT NULL,       -- 'api' 또는 'login'
        -- connection_type='api' 일 때 쓰는 항목
        api_vendor_id TEXT,
        api_access_key TEXT,
        api_secret_key TEXT,
        -- connection_type='login' 일 때 쓰는 항목 (브라우저 자동화용)
        login_id TEXT,
        login_password TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """,
    """
    -- 취소요청/반품요청 (쿠팡 공식 API: returnRequests, cancelType=CANCEL/RETURN
    -- 둘 다 같은 API라서 표 하나에 claim_type으로 구분해서 저장합니다)
    CREATE TABLE IF NOT EXISTS claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_name TEXT NOT NULL,
        market_account_id INTEGER REFERENCES market_accounts(id),
        claim_type TEXT NOT NULL,            -- 'CANCEL' 또는 'RETURN'
        receipt_id TEXT NOT NULL,            -- 쿠팡 접수번호(receiptId)
        market_order_id TEXT,                -- 쿠팡 주문번호(orderId)
        receipt_status TEXT,                 -- 처리 상태 (쿠팡 receiptStatus)
        reason_category1 TEXT,
        reason_category2 TEXT,
        reason_detail TEXT,
        requested_at TEXT,                   -- 접수일시 (쿠팡 createdAt)
        complete_confirm_type TEXT,
        complete_confirm_date TEXT,
        raw_response_json TEXT,
        first_collected_at TEXT NOT NULL,
        last_updated_at TEXT NOT NULL,
        UNIQUE (market_name, claim_type, receipt_id)
    )
    """,
    """
    -- 교환요청 (쿠팡 공식 API: exchangeRequests)
    CREATE TABLE IF NOT EXISTS exchange_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_name TEXT NOT NULL,
        market_account_id INTEGER REFERENCES market_accounts(id),
        exchange_id TEXT NOT NULL,           -- 쿠팡 교환접수번호
        market_order_id TEXT,
        status TEXT,
        requested_at TEXT,
        raw_response_json TEXT,
        first_collected_at TEXT NOT NULL,
        last_updated_at TEXT NOT NULL,
        UNIQUE (market_name, exchange_id)
    )
    """,
    """
    -- 상품문의 (쿠팡 공식 API: onlineInquiries - 구매자가 상품 페이지에서 남긴 질문)
    CREATE TABLE IF NOT EXISTS product_inquiries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_name TEXT NOT NULL,
        market_account_id INTEGER REFERENCES market_accounts(id),
        inquiry_id TEXT NOT NULL,
        market_item_id TEXT,                 -- 상품 ID (쿠팡 productId)
        content TEXT,
        inquiry_at TEXT,
        answered INTEGER,                    -- 0 또는 1 (답변 완료 여부)
        raw_response_json TEXT,
        first_collected_at TEXT NOT NULL,
        last_updated_at TEXT NOT NULL,
        UNIQUE (market_name, inquiry_id)
    )
    """,
    """
    -- 콜센터문의 (쿠팡 공식 API: callCenterInquiries - 고객센터로 들어온 문의)
    CREATE TABLE IF NOT EXISTS call_center_inquiries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_name TEXT NOT NULL,
        market_account_id INTEGER REFERENCES market_accounts(id),
        inquiry_id TEXT NOT NULL,
        market_order_id TEXT,
        inquiry_status TEXT,
        partner_counseling_status TEXT,
        content TEXT,
        buyer_phone TEXT,
        inquiry_at TEXT,
        raw_response_json TEXT,
        first_collected_at TEXT NOT NULL,
        last_updated_at TEXT NOT NULL,
        UNIQUE (market_name, inquiry_id)
    )
    """,
]



# ----------------------------------------------------------
# 나중에 새로 추가된 컬럼 목록 (기존에 만들어둔 데이터베이스 파일에도
# 자동으로 추가해주기 위한 목록입니다. 표에 처음부터 있던 컬럼은 여기에
# 넣지 않습니다 - CREATE TABLE 문에 이미 있으니까요)
# ----------------------------------------------------------
ADDITIONAL_COLUMNS = {
    "orders": [
        ("shipping_fee", "INTEGER"),
        ("settlement_amount", "INTEGER"),
        ("delivery_company_code", "TEXT"),
        ("invoice_number", "TEXT"),
        ("shipped_at", "TEXT"),
        ("market_account_id", "INTEGER"),
        ("orderer_name", "TEXT"),
        ("orderer_phone", "TEXT"),
        ("paid_at", "TEXT"),
        ("remote_area", "INTEGER"),
        ("market_delivery_company_name", "TEXT"),
        ("market_invoice_number", "TEXT"),
        ("quickstar_order_no", "TEXT"),
        ("quickstar_invoice", "TEXT"),
        ("quickstar_submitted_at", "TEXT"),
        ("cs_memo", "TEXT"),
    ],
    "order_items": [
        ("seller_product_code", "TEXT"),
        ("seller_option_code", "TEXT"),
        ("delivery_charge_type_name", "TEXT"),
        ("estimated_shipping_date", "TEXT"),
    ],
    "shipping_information": [
        ("name_check_message", "TEXT"),
        ("phone_check_message", "TEXT"),
        ("pccc_check_message", "TEXT"),
        ("zip_check_message", "TEXT"),
        # 통관번호/우편번호 오류 시 자동으로 오류문구(1·2번)를 보낸 뒤, 같은 정보(스냅샷
        # 해시)로 또 보내지 않도록 마지막으로 자동발송한 스냅샷 해시를 기록합니다.
        ("customs_error_sms_sent", "TEXT"),
    ],
    "market_accounts": [
        ("platform", "TEXT"),
        # 상품문의 답변을 등록할 때 쿠팡이 "누가 답변했는지"(replyBy)를 요구합니다.
        # 그 값이 판매자 WING 아이디입니다.
        ("wing_id", "TEXT"),
    ],
    "product_inquiries": [
        # 문의가 어떤 상품/옵션에 대한 것인지 알아내기 위한 값들입니다.
        # (쿠팡 문의 응답에는 상품명이 없고 ID만 들어있어서, 이 ID로 우리
        #  주문 데이터에서 상품명/옵션을 찾아옵니다)
        ("seller_product_id", "TEXT"),      # 쿠팡 sellerProductId (판매자 상품 단위)
        ("vendor_item_id", "TEXT"),         # 쿠팡 vendorItemId (옵션 단위)
        ("order_ids", "TEXT"),              # 관련 주문번호들, 쉼표로 이어붙임
        # 답변 내용 (쿠팡에 이미 등록된 답변이거나, 우리가 등록한 답변)
        ("answer_content", "TEXT"),
        ("answered_at", "TEXT"),
    ],
}


def _add_missing_columns(connection: sqlite3.Connection) -> None:
    """이미 만들어져 있던 표에, 나중에 새로 생긴 컬럼이 빠져있으면 추가해줍니다."""
    for table_name, columns in ADDITIONAL_COLUMNS.items():
        existing_columns = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})")
        }
        for column_name, column_type in columns:
            if column_name not in existing_columns:
                connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def init_db() -> None:
    """필요한 테이블이 없으면 만듭니다. 이미 있으면 빠진 컬럼만 추가합니다."""
    connection = get_connection()
    try:
        for statement in CREATE_TABLE_STATEMENTS:
            connection.execute(statement)
        _add_missing_columns(connection)
        connection.commit()
    finally:
        connection.close()


if __name__ == "__main__":
    # 이 파일을 직접 실행하면(예: python database.py) 테이블만 만들고 끝냅니다.
    init_db()
    print(f"데이터베이스 초기화 완료: {config.DB_PATH}")
