# ==========================================================
# 회귀 테스트: 재수집이 기존 PII(수취인·구매자·통관번호 등)를 빈 값으로 덮지 않는다
# ----------------------------------------------------------
# 배경(2026-11 실측 사고):
#   쿠팡은 개인정보(수취인명·전화·통관부호·구매자명 등)를 주문 직후 '잠깐'만 주고
#   그 뒤 재조회하면 빈 값("")으로 준다. 과거 코드가 그 빈 값으로 기존 PII를 덮어써
#   발송대기/배송중 수백 건의 수취인·구매자 정보가 통째로 지워졌다.
#   → order_repository 의 쓰기 경로가 COALESCE(NULLIF(?,''), 기존값) 으로 '빈 값은 무시'
#     하도록 고쳤다. 이 테스트는 그 보호가 실수로 풀리면 즉시 빨간불이 켜지게 한다.
#
# 프로덕션 DB는 절대 건드리지 않는다: DATABASE_URL을 비우고 임시 SQLite 파일만 쓴다.
# 실행:  .venv\Scripts\python.exe tests\test_pii_preserve.py
# ==========================================================
import os
import sys
import tempfile

# ★ 앱 모듈을 import 하기 "전에" 환경을 임시 SQLite로 고정 (프로덕션 Supabase 차단)
_TMP = tempfile.NamedTemporaryFile(prefix="pii_test_", suffix=".db", delete=False)
_TMP.close()
os.environ["DATABASE_URL"] = ""          # use_postgres() -> False
os.environ["DB_PATH"] = _TMP.name
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
# 혹시 .env 등으로 이미 채워졌다면 강제로 비움(테스트 격리 보장)
config.DATABASE_URL = ""
config.DB_PATH = _TMP.name

import database  # noqa: E402
from repositories import order_repository as repo  # noqa: E402

assert not config.use_postgres(), "테스트가 프로덕션(PostgreSQL)에 붙으려 함 — 중단"


def _fail(msg):
    print(f"  [FAIL] {msg}")
    return False


def _ok(msg):
    print(f"  [ok]   {msg}")
    return True


def run():
    database.init_db()
    passed = True

    # 1) 새 주문을 PII와 함께 저장
    oid = repo.insert_order(
        market_name="테스트몰", market_order_id="TESTPII001", shipment_box_id="BOX1",
        ordered_at="2026-11-10T10:00:00+09:00", market_status="ACCEPT", raw_response_json="{}",
        orderer_name="홍길동", orderer_phone="010-1111-2222",
    )
    repo.upsert_shipping_information(oid, {
        "receiver_name": "김수취", "receiver_phone_raw": "010-3333-4444",
        "customs_phone": "010-5555-6666", "pccc": "P123456789012",
        "zip_code": "01234", "address_basic": "서울시 어딘가", "address_detail": "101동 202호",
    })

    def read():
        c = database.get_connection()
        o = c.execute("SELECT orderer_name, orderer_phone FROM orders WHERE id=?", (oid,)).fetchone()
        s = c.execute(
            "SELECT receiver_name, receiver_phone_raw, customs_phone, pccc, zip_code,"
            " address_basic, address_detail FROM shipping_information WHERE order_id=?", (oid,)
        ).fetchone()
        c.close()
        return o, s

    o, s = read()
    passed &= _ok("초기 저장") if (o["orderer_name"] == "홍길동" and s["receiver_name"] == "김수취") \
        else _fail("초기 저장이 안 됨")

    # 2) ★핵심: 쿠팡이 빈 값으로 준 재수집 — 기존 PII 가 유지돼야 한다
    repo.update_order_from_market(
        oid, market_status="INSTRUCT", raw_response_json="{}",
        orderer_name="", orderer_phone="",          # 쿠팡이 비워서 줌
    )
    repo.upsert_shipping_information(oid, {
        "receiver_name": "", "receiver_phone_raw": "", "customs_phone": "",
        "pccc": "", "zip_code": "", "address_basic": "", "address_detail": "",
    })
    o, s = read()
    checks = {
        "orderer_name": o["orderer_name"] == "홍길동",
        "orderer_phone": o["orderer_phone"] == "010-1111-2222",
        "receiver_name": s["receiver_name"] == "김수취",
        "receiver_phone_raw": s["receiver_phone_raw"] == "010-3333-4444",
        "customs_phone": s["customs_phone"] == "010-5555-6666",
        "pccc": s["pccc"] == "P123456789012",
        "zip_code": s["zip_code"] == "01234",
        "address_basic": s["address_basic"] == "서울시 어딘가",
        "address_detail": s["address_detail"] == "101동 202호",
    }
    for field, good in checks.items():
        passed &= _ok(f"빈 재수집 후 {field} 보존") if good \
            else _fail(f"빈 재수집이 {field} 를 덮어버림 ← 가드 깨짐!")

    # 3) 진짜 새 값이 오면 '정상적으로' 갱신돼야 한다(가드가 정당한 업데이트를 막으면 안 됨)
    repo.update_order_from_market(
        oid, market_status="DELIVERING", raw_response_json="{}",
        orderer_name="홍갱신", orderer_phone="010-7777-8888",
    )
    repo.upsert_shipping_information(oid, {
        "receiver_name": "김갱신", "receiver_phone_raw": "", "customs_phone": "",
        "pccc": "", "zip_code": "", "address_basic": "", "address_detail": "",
    })
    o, s = read()
    passed &= _ok("새 값은 정상 갱신(orderer_name)") if o["orderer_name"] == "홍갱신" \
        else _fail("새 값 갱신이 안 됨(가드가 과함)")
    passed &= _ok("새 값은 정상 갱신(receiver_name)") if s["receiver_name"] == "김갱신" \
        else _fail("새 값 갱신이 안 됨(가드가 과함)")
    # 같은 호출에서 빈 값으로 보낸 다른 필드는 여전히 보존돼야 함
    passed &= _ok("부분 갱신 시 빈 값 필드 보존(pccc)") if s["pccc"] == "P123456789012" \
        else _fail("부분 갱신이 pccc 를 덮음")

    return passed


if __name__ == "__main__":
    try:
        ok = run()
    finally:
        try:
            os.unlink(_TMP.name)
        except OSError:
            pass
    print("\n=== 결과:", "PASS ✅ (PII 보호 정상)" if ok else "FAIL ❌ (가드 깨짐!)", "===")
    sys.exit(0 if ok else 1)
