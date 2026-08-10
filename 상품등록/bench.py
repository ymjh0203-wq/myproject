# ============================================================
# bench.py  —  벤치마킹 소싱 진입점
# ------------------------------------------------------------
# 잘 팔리는 "기준 상품(씨앗)"의 이미지로 타오바오에서 유사 상품 N개를 찾아
# 각각 추출해서 products_raw 에 저장합니다.
#
# 준비:
#   pip install -r requirements.txt && playwright install chromium
#   python 타오바오_로그인_설정.bat        # (최초 1회) 타오바오 로그인
#   .env 의 DATABASE_URL + python apply_migration.py   # 표 준비(001,002)
#
# 실행 (상품등록 폴더 안에서):
#   # 한국 마켓 상품 URL 로 (대표이미지 자동 추출)
#   python bench.py --url "https://smartstore.naver.com/..." -n 3
#   # 로컬 이미지 파일로
#   python bench.py --image "C:\\path\\to\\photo.jpg" -n 5
#
#   옵션:
#     -n / --num     찾을 후보 개수 (기본 3)
#     --manual       이미지검색을 창에서 직접(캡차/차단 시 폴백)
#     --dry-run      DB에 저장하지 않고 찾은 후보/추출 결과만 출력
# ============================================================

import argparse
import json
import logging
import sys

import seed as seed_mod
from collector.taobao import CaptchaDetected, collect_taobao
from collector.taobao_image_search import image_search

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bench")


def parse_args():
    ap = argparse.ArgumentParser(description="벤치마킹 소싱: 기준 상품 이미지로 타오바오 유사 상품 찾기")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="한국 마켓(스마트스토어/쿠팡 등) 상품 URL")
    g.add_argument("--image", help="로컬 이미지 파일 경로")
    ap.add_argument("-n", "--num", type=int, default=3, help="찾을 후보 개수 (기본 3)")
    ap.add_argument("--manual", action="store_true", help="이미지검색을 창에서 직접 처리")
    ap.add_argument("--dry-run", action="store_true", help="DB 저장 없이 결과만 출력")
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    # 1) 씨앗 이미지 확보
    try:
        if args.url:
            logger.info("기준 상품 URL에서 대표이미지 추출 중...")
            seed = seed_mod.image_from_url(args.url)
        else:
            seed = seed_mod.image_from_file(args.image)
        logger.info("씨앗 이미지: %s", seed["seed_image_path"])
    except Exception as e:
        logger.error("씨앗 이미지 확보 실패: %s", e)
        sys.exit(1)

    # 2) 타오바오 이미지검색 → 후보 URL
    try:
        candidates = image_search(seed["seed_image_path"], n=args.num, manual=args.manual)
    except CaptchaDetected as e:
        logger.warning("이미지검색 중단(캡차/차단): %s", e)
        print("\n[안내] 자동으로 뚫지 않았습니다. --manual 로 다시 실행해주세요.")
        sys.exit(2)
    except Exception as e:
        logger.exception("이미지검색 실패: %s", e)
        sys.exit(1)

    if not candidates:
        print("찾은 후보가 없습니다. --manual 로 재시도하거나 다른 이미지를 써보세요.")
        sys.exit(0)

    print(f"\n[후보 {len(candidates)}개]")
    for i, u in enumerate(candidates, start=1):
        print(f"  {i}. {u}")

    # 3) 각 후보를 추출
    results = []
    for rank, url in enumerate(candidates, start=1):
        try:
            logger.info("후보 %d 추출 중...", rank)
            data = collect_taobao(url)
            data["_match_rank"] = rank
            results.append(data)
        except CaptchaDetected as e:
            logger.warning("후보 %d 추출 중 캡차: %s (건너뜀)", rank, e)
        except Exception as e:
            logger.warning("후보 %d 추출 실패: %s (건너뜀)", rank, e)

    # --dry-run: 저장 안 하고 출력만
    if args.dry_run:
        print("\n[dry-run] 저장하지 않고 추출 결과만 출력합니다:\n")
        print(json.dumps({"seed": seed, "results": results}, ensure_ascii=False, indent=2))
        return

    # 4) 저장 (씨앗 기록 → 각 후보 저장 → 씨앗/순위 연결)
    from db import get_conn, insert_benchmark_seed, link_to_seed, upsert_product_raw

    try:
        conn = get_conn()
    except Exception as e:
        logger.error("DB 연결 실패: %s", e)
        print("추출은 됐지만 저장은 못 했습니다. .env 의 DATABASE_URL 을 확인해주세요.")
        sys.exit(1)

    try:
        seed_id = insert_benchmark_seed(conn, seed)
        saved = 0
        for data in results:
            rank = data.pop("_match_rank", None)
            pid = upsert_product_raw(conn, data)
            link_to_seed(conn, pid, seed_id, rank)
            saved += 1
            print(f"  저장: products_raw #{pid} (순위 {rank}) - {data.get('title_original')}")
        print(f"\n[완료] 씨앗 #{seed_id} 기준으로 {saved}개 후보 저장")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
