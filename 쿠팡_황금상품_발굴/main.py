# ==========================================================
# 실행 파일 (main.py)
# ----------------------------------------------------------
# *** 이 프로그램을 쓸 때는 이 파일만 실행하면 됩니다. ***
#
#   실행 방법 (명령 프롬프트 / PowerShell에서):
#       python main.py
#
# 하는 일:
#   1. products_template.csv 에서 후보 상품을 읽는다
#   2. 크무비 조건(부피/무게/가격)으로 걸러낸다
#   3. 통과한 상품만 네이버 API로 검색량 / 계절성을 조회한다
#      (API 키가 없으면 이 단계는 건너뛰고 0점 처리, 에러 나지 않음)
#   4. 종합 점수를 매겨서 output/골든상품_결과.csv 로 저장한다
# ==========================================================

import csv
import os
import time

import config
import filters
import naver_searchad
import naver_datalab
import scorer
import blacklist_filter
import csv_utils


NUMERIC_FIELDS = ["가로_cm", "세로_cm", "높이_cm", "실중량_kg", "판매가_원"]


def load_products(csv_path):
    """CSV 파일에서 상품 목록을 읽어옵니다. 잘못된 줄은 건너뛰고 경고만 출력합니다."""
    if not os.path.exists(csv_path):
        print(f"[오류] 입력 파일을 찾을 수 없습니다: {csv_path}")
        print("      products_template.csv 파일이 main.py와 같은 폴더에 있는지 확인해주세요.")
        return []

    try:
        rows = csv_utils.read_csv_rows(csv_path)
    except (UnicodeDecodeError, UnicodeError):
        print(f"[오류] '{csv_path}' 파일의 글자 인코딩을 인식하지 못했습니다.")
        print("      엑셀에서 '다른 이름으로 저장' -> 파일 형식을 'CSV UTF-8(쉼표로 분리)'로 다시 저장해주세요.")
        return []

    products = []
    for row_number, row in enumerate(rows, start=2):  # 2행부터 (1행은 헤더)
        try:
            product = dict(row)
            for field in NUMERIC_FIELDS:
                product[field] = float(row[field])
            products.append(product)
        except (KeyError, ValueError, TypeError) as e:
            print(f"[경고] {row_number}행을 건너뜁니다 (데이터 형식 오류): {e}")

    return products


def process_products(products):
    """상품 목록을 필터링하고, 통과한 상품은 점수까지 계산합니다."""
    results = []
    filtered_out_count = 0

    searchad_ready = naver_searchad.is_configured()
    datalab_ready = naver_datalab.is_configured()
    blacklist_terms = blacklist_filter.load_blacklist(config.BLACKLIST_CSV_PATH)

    if not searchad_ready:
        print("[안내] 검색광고 API 키가 없어 '검색량' 조회를 건너뜁니다. (config.py에서 키를 채우면 활성화됩니다)")
    if not datalab_ready:
        print("[안내] 데이터랩 API 키가 없어 '계절성' 조회를 건너뜁니다. (config.py에서 키를 채우면 활성화됩니다)")
    if blacklist_terms:
        print(f"[안내] 제외키워드 {len(blacklist_terms)}개를 기준으로 브랜드/금지품목을 걸러냅니다.")

    for product in products:
        name = product.get("상품명", "(이름없음)")
        keyword = product.get("검색키워드") or name

        blocked, blocked_term, blocked_category = blacklist_filter.check_blacklist(
            f"{name} {keyword}", blacklist_terms
        )

        passed, reason, calculated = filters.check_kmubi(product)

        if blocked:
            passed = False
            reason = f"제외키워드 포함 [{blocked_category}]: '{blocked_term}'"

        if not passed:
            filtered_out_count += 1
            results.append(
                {
                    "상품명": name,
                    "검색키워드": keyword,
                    "크무비통과": "N",
                    "사유": reason,
                    "부피합_cm": calculated["부피합_cm"],
                    "적용중량_kg": calculated["적용중량_kg"],
                    "판매가_원": int(product["판매가_원"]),
                    "월간검색량": "",
                    "검색량조회상태": "-",
                    "계절성_변동계수": "",
                    "계절성_최고월": "",
                    "계절성_최저월": "",
                    "계절성조회상태": "-",
                    "검색량점수": "",
                    "계절성점수": "",
                    "가격점수": "",
                    "크무비강도점수": "",
                    "총점": "",
                }
            )
            continue

        # --- 여기부터는 크무비 조건을 통과한 상품만 처리 ---
        search_volume, volume_status = naver_searchad.get_monthly_search_volume(keyword)
        time.sleep(0.2)  # API를 너무 빠르게 연달아 호출하지 않도록 살짝 대기

        seasonality, season_status = naver_datalab.get_seasonality(keyword)
        time.sleep(0.2)

        variation_coefficient = seasonality["변동계수"] if seasonality else None

        score = scorer.calc_total_score(
            search_volume=search_volume,
            variation_coefficient=variation_coefficient,
            price_won=product["판매가_원"],
            size_sum_cm=calculated["부피합_cm"],
            applied_weight_kg=calculated["적용중량_kg"],
        )

        results.append(
            {
                "상품명": name,
                "검색키워드": keyword,
                "크무비통과": "Y",
                "사유": reason,
                "부피합_cm": calculated["부피합_cm"],
                "적용중량_kg": calculated["적용중량_kg"],
                "판매가_원": int(product["판매가_원"]),
                "월간검색량": search_volume if search_volume is not None else "",
                "검색량조회상태": volume_status,
                "계절성_변동계수": seasonality["변동계수"] if seasonality else "",
                "계절성_최고월": seasonality["최고월"] if seasonality else "",
                "계절성_최저월": seasonality["최저월"] if seasonality else "",
                "계절성조회상태": season_status,
                "검색량점수": score["검색량점수"],
                "계절성점수": score["계절성점수"],
                "가격점수": score["가격점수"],
                "크무비강도점수": score["크무비강도점수"],
                "총점": score["총점"],
            }
        )

    return results, filtered_out_count


def save_results(results, output_path):
    """결과를 CSV로 저장합니다. 총점이 높은 순으로 정렬합니다."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 총점이 있는(=크무비 통과한) 상품을 먼저, 점수 높은 순으로 정렬
    def sort_key(row):
        return row["총점"] if isinstance(row["총점"], (int, float)) else -1

    results_sorted = sorted(results, key=sort_key, reverse=True)

    fieldnames = list(results_sorted[0].keys()) if results_sorted else []

    try:
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results_sorted)
    except PermissionError:
        print(f"\n[오류] '{output_path}' 파일에 저장하지 못했습니다.")
        print("       엑셀 등 다른 프로그램에서 이 파일을 열어두신 것 같아요. 파일을 닫고 다시 실행해주세요.")

    return results_sorted


def main():
    print("=" * 50)
    print("쿠팡 황금상품(크무비) 발굴 프로그램을 시작합니다")
    print("=" * 50)

    products = load_products(config.INPUT_CSV_PATH)
    if not products:
        print("\n처리할 상품이 없어 프로그램을 종료합니다.")
        return

    print(f"\n총 {len(products)}개 상품을 불러왔습니다. 크무비 필터링을 시작합니다...\n")

    results, filtered_out_count = process_products(products)
    passed_count = len(results) - filtered_out_count

    results_sorted = save_results(results, config.OUTPUT_CSV_PATH)

    print("\n" + "=" * 50)
    print("처리 완료!")
    print(f"  - 전체 상품 수     : {len(products)}개")
    print(f"  - 크무비 통과      : {passed_count}개")
    print(f"  - 크무비 미통과    : {filtered_out_count}개")
    print(f"  - 결과 파일 위치   : {config.OUTPUT_CSV_PATH}")
    print("=" * 50)

    top_passed = [r for r in results_sorted if r["크무비통과"] == "Y"][:5]
    if top_passed:
        print("\n[상위 후보 미리보기]")
        for i, row in enumerate(top_passed, start=1):
            print(f"  {i}. {row['상품명']} - 총점 {row['총점']}점 (검색량 {row['월간검색량']})")


if __name__ == "__main__":
    main()
