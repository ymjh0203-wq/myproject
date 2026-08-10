# ============================================================
# collect.py
# 타오바오 상세 URL 1개를 받아 수집 → products_raw 에 저장하는 실행 진입점.
# ------------------------------------------------------------
# 준비:
#   pip install -r requirements.txt
#   playwright install chromium
#   .env 에 DATABASE_URL 설정 + migrations 적용(python apply_migration.py)
#
# 실행 (상품등록 폴더 안에서):
#   python collect.py "https://item.taobao.com/item.htm?id=..."
#
# 처음 실행하면 브라우저 창이 뜹니다.
#   - 로그인/캡차 화면이면: 그 창에서 직접 로그인/캡차 해결 후 다시 실행하세요.
#   - 한 번 로그인해두면 .browser_profile 에 저장되어 다음부터 유지됩니다.
# ============================================================

import json
import logging
import sys

from collector.taobao import CaptchaDetected, collect_taobao
from db import get_conn, upsert_product_raw

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("collect")


def main() -> None:
    # --dry-run: DB에 저장하지 않고 추출 결과만 출력(로그인 후 셀렉터 점검용)
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv[1:]

    if not args:
        print('사용법: python collect.py "<타오바오 상세페이지 URL>" [--dry-run]')
        sys.exit(1)

    url = args[0].strip()
    if "taobao.com" not in url and "tmall.com" not in url:
        print("[경고] 타오바오/티몰 상세페이지 URL 이 아닌 것 같습니다:", url)

    # 1) 수집
    try:
        data = collect_taobao(url)
    except CaptchaDetected as e:
        logger.warning("수집 중단(캡차/로그인 필요): %s", e)
        print("\n[안내] 자동으로 뚫지 않았습니다. 브라우저에서 직접 처리 후 다시 실행해주세요.")
        sys.exit(2)
    except Exception as e:
        logger.exception("수집 실패: %s", e)
        sys.exit(1)

    # 어떤 값이 비었는지 사용자에게 알려줌(셀렉터 튜닝 판단용)
    empty = [k for k in ("title_original", "price_original", "image_urls") if not data.get(k)]
    if empty:
        logger.warning("추출하지 못한 필드: %s (셀렉터 조정이 필요할 수 있음)", ", ".join(empty))

    # --dry-run 이면 추출 결과만 보여주고 종료(저장 안 함)
    if dry_run:
        print("\n[dry-run] 저장하지 않고 추출 결과만 출력합니다:\n")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    # 2) 저장
    try:
        conn = get_conn()
    except Exception as e:
        logger.error("DB 연결 실패: %s", e)
        print("추출은 됐지만 저장은 못 했습니다. .env 의 DATABASE_URL 을 확인해주세요.")
        sys.exit(1)

    try:
        new_id = upsert_product_raw(conn, data)
        logger.info("저장 완료 → products_raw.id = %s", new_id)
        print(f"\n[완료] products_raw #{new_id} 저장")
        print(f"   상품명: {data.get('title_original')}")
        print(f"   가격(CNY): {data.get('price_original')}")
        print(f"   이미지 수: {len(data.get('image_urls') or [])}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
