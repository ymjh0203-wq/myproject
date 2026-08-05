# ==========================================================
# 설정 파일 (config.py)
# ----------------------------------------------------------
# .env 파일에 적어둔 값들을 읽어와서 프로그램 전체에서 쓸 수 있게 정리해둔 곳입니다.
# .env 파일이 없어도 프로그램이 죽지 않고, 기본값(mock 모드)으로 동작합니다.
# ==========================================================

import os

from dotenv import load_dotenv

# 이 config.py 파일이 있는 폴더를 프로그램의 "기준 폴더"로 삼습니다.
# 어느 위치(폴더)에서 프로그램을 실행하더라도 항상 같은 곳에서
# .env 파일을 읽고, 같은 곳에 데이터베이스 파일을 만들기 위함입니다.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# .env 파일을 읽어서 환경변수로 등록합니다.
# (파일이 없으면 그냥 아무 일도 안 일어납니다. 에러 아님.)
load_dotenv(os.path.join(BASE_DIR, ".env"))


# ----------------------------------------------------------
# 실행 모드: "mock"(가상 데이터) 또는 "real"(실제 쿠팡 API)
# ----------------------------------------------------------
APP_MODE = os.environ.get("APP_MODE", "mock").strip().lower()


def is_mock_mode() -> bool:
    """지금이 가상(Mock) 모드인지 확인합니다."""
    return APP_MODE != "real"


# ----------------------------------------------------------
# 쿠팡 오픈API 인증 정보 (real 모드에서만 사용)
# ----------------------------------------------------------
COUPANG_VENDOR_ID = os.environ.get("COUPANG_VENDOR_ID", "")
COUPANG_ACCESS_KEY = os.environ.get("COUPANG_ACCESS_KEY", "")
COUPANG_SECRET_KEY = os.environ.get("COUPANG_SECRET_KEY", "")

# ----------------------------------------------------------
# 관세청 유니패스 오픈API 인증키 (추후 공식 통관검증 연동 시 사용)
# ----------------------------------------------------------
UNIPASS_API_KEY = os.environ.get("UNIPASS_API_KEY", "")

# ----------------------------------------------------------
# 퀵스타(배송대행지) 오픈API 인증 정보
# ----------------------------------------------------------
# QUICKSTAR_API_KEY: 접수/조회 API의 "user-session" 헤더에 넣는 토큰키입니다.
#   (2026-07-23에 읽기전용 조회 API로 실제 인증 통과를 확인했습니다)
QUICKSTAR_API_KEY = os.environ.get("QUICKSTAR_API_KEY", "")
QUICKSTAR_USER_ID = os.environ.get("QUICKSTAR_USER_ID", "")

# ----------------------------------------------------------
# 데이터베이스 파일 경로 (기본값: 이 프로그램 폴더 안의 order_management.db)
# ----------------------------------------------------------
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "order_management.db"))

# ----------------------------------------------------------
# 클라우드 데이터베이스(Supabase PostgreSQL) 연결 문자열
# ----------------------------------------------------------
# 이 값이 비어 있으면 예전처럼 위의 SQLite 파일(order_management.db)을 씁니다.
# 값이 채워져 있으면 그 대신 Supabase PostgreSQL에 접속해서 모든 데이터를
# 클라우드에 저장/조회합니다. (여러 컴퓨터에서 같은 데이터를 볼 수 있게 됩니다)
#
# Supabase 대시보드 → Settings → Database → Connection string(URI 탭) 값을
# .env 파일의 DATABASE_URL 에 붙여넣으면 됩니다. 예:
#   DATABASE_URL=postgresql://postgres.xxxx:비밀번호@aws-...pooler.supabase.com:5432/postgres
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def use_postgres() -> bool:
    """지금 클라우드(Supabase PostgreSQL)를 쓰는 중인지 확인합니다."""
    return bool(DATABASE_URL)

# ----------------------------------------------------------
# 문자 발송 게이트웨이 (폰의 Simple SMS Gateway 앱, Tailscale 경유)
# ----------------------------------------------------------
# 값이 있으면 모든 문자(수동/자동)를 이 주소로 POST해서 폰이 발송합니다.
# 비어 있으면 예전처럼 Windows 'Phone Link' 자동화로 보냅니다.
# 예: http://100.119.62.23:8080/send-sms  (100.x는 폰의 Tailscale IP)
SMS_GATEWAY_URL = os.environ.get("SMS_GATEWAY_URL", "")

# ----------------------------------------------------------
# 화면에서 개인정보(전화번호, 통관고유부호)를 마스킹해서 보여줄지 여부
# 기본값은 True(마스킹함). 설정 화면에서 나중에 끌 수 있게 만들 예정입니다.
# ----------------------------------------------------------
MASK_PERSONAL_INFO_DEFAULT = True
