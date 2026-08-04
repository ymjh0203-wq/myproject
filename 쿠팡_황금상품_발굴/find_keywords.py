# ==========================================================
# 키워드 발굴 실행 파일 (find_keywords.py)
# ----------------------------------------------------------
# *** 특정 상품이 아니라, "어떤 분야에 황금 키워드가 있는지" 찾고 싶을 때 이 파일을 실행하세요. ***
#
#   실행 방법:
#       python find_keywords.py
#
# 하는 일:
#   1. seed_keywords.csv 에 적어둔 대표 키워드들을 읽는다
#   2. 검색광고 API의 "연관키워드" 기능으로 각 대표 키워드마다
#      수십~수백 개의 관련 키워드 + 검색량 + 경쟁정도를 받아온다
#   3. 여러 시드에서 겹치는 키워드는 하나로 합친다
#   4. 월간 검색량 6000 이상만 남긴다
#   5. 검색량 상위 키워드는 데이터랩으로 3년 계절성까지 확인한다
#   6. 점수를 매겨서 output/키워드발굴_결과.csv 로 저장한다
#
# *** 이 기능은 반드시 네이버 검색광고 API 키가 있어야 동작합니다.
#     (연관키워드 자체를 그 API에서만 받아올 수 있기 때문입니다)
#     키가 없으면 안내만 출력하고 빈 결과로 조용히 종료합니다. (에러로 죽지 않음) ***
# ==========================================================

import csv
import datetime
import os
import time

import config
import naver_searchad
import naver_datalab
import scorer
import blacklist_filter
import csv_utils
import keyword_screening
import china_sourcing
import risk_classifier
import excel_export

# 결과 CSV/xlsx의 기본 컬럼 목록 (결과가 0건이어도 헤더가 항상 이 순서로 나오도록 고정해둠)
# 목표월별 계절지수/예상검색량 컬럼은 몇 달을 물어봤는지에 따라 개수가 달라지므로
# build_result_fieldnames()가 실행 시점에 동적으로 끼워 넣습니다.
_BASE_FIELDNAMES_HEAD = [
    "키워드", "원본시드키워드", "월간검색량", "PC검색량", "모바일검색량", "경쟁정도", "목표월들",
    "계절성_변동계수", "계절성_최고월", "계절성_최저월", "계절성조회상태",
]
_BASE_FIELDNAMES_TAIL = [
    "계절성최적월_추정", "계절성최적월지수_추정",
    "검색량점수", "경쟁정도점수", "계절성점수", "총점",
    "카테고리관련성점수_추정", "감지카테고리_추정", "관련성판단근거_추정", "정보성키워드여부_추정", "정보성판단근거_추정",
    "소형저가가능성_추정", "소형저가판단근거_추정", "브랜드포함여부", "매칭브랜드", "크무비가능성점수_추정", "크무비가능성등급_추정",
    "크무비판단근거_추정", "실제크기무게확인필요", "해외구매대행적합도점수_추정", "제외검토사유",
    "중국소싱가능성점수_추정", "소싱가능성등급_추정", "중국소싱판단근거_추정", "중국사이트직접확인필요여부",
    "위험요소_추정", "종합위험도등급_추정", "데이터신뢰도_추정", "최종추천점수_종합", "추천등급", "추천근거",
]


def build_result_fieldnames(target_months):
    """목표월 개수에 맞춰 'N월_계절지수_추정'/'N월_예상검색량_추정' 컬럼을 끼워 넣은 전체 컬럼 목록을 만듭니다."""
    month_cols = []
    for month in target_months:
        month_cols.append(f"{month}월_계절지수_추정")
        month_cols.append(f"{month}월_예상검색량_추정")
    return _BASE_FIELDNAMES_HEAD + month_cols + _BASE_FIELDNAMES_TAIL


# API 키가 없을 때 등, 목표월을 아직 안 물어본 상태에서 헤더가 필요할 때 쓰는 기본값
# (API 키 없음 폴백에서만 쓰이는 기본 헤더라, 오늘 기준 다음 3개월로 계산해둠)
_this_month = datetime.date.today().month
RESULT_FIELDNAMES = build_result_fieldnames([(_this_month + i - 1) % 12 + 1 for i in range(1, 4)])


def load_seed_keywords(csv_path):
    """seed_keywords.csv에서 시드 키워드 목록을 읽어옵니다."""
    if not os.path.exists(csv_path):
        print(f"[오류] 시드 키워드 파일을 찾을 수 없습니다: {csv_path}")
        return []

    try:
        rows = csv_utils.read_csv_rows(csv_path)
    except (UnicodeDecodeError, UnicodeError):
        print(f"[오류] '{csv_path}' 파일의 글자 인코딩을 인식하지 못했습니다.")
        print("      엑셀에서 '다른 이름으로 저장' -> 파일 형식을 'CSV UTF-8(쉼표로 분리)'로 다시 저장해주세요.")
        return []

    seeds = []
    for row in rows:
        keyword = (row.get("시드키워드") or "").strip()
        if keyword:
            seeds.append(keyword)
    return seeds


def collect_related_keywords(seeds):
    """모든 시드 키워드에 대해 연관키워드를 모으고, 중복은 하나로 합칩니다."""
    merged = {}  # 키워드 문자열 -> 결과 딕셔너리

    for seed in seeds:
        related, status = naver_searchad.get_related_keywords(
            seed, max_results=config.MAX_RELATED_KEYWORDS_PER_SEED
        )
        time.sleep(0.2)

        if related is None:
            print(f"  - '{seed}': {status}")
            continue

        print(f"  - '{seed}': 연관키워드 {len(related)}개 수집 ({status})")

        for item in related:
            kw = item["키워드"]
            if kw not in merged:
                merged[kw] = {**item, "원본시드키워드": seed}
            else:
                # 이미 있으면, 시드키워드 출처만 추가로 기록 (검색량 등은 그대로 유지)
                existing_seeds = merged[kw]["원본시드키워드"]
                if seed not in existing_seeds.split(", "):
                    merged[kw]["원본시드키워드"] = f"{existing_seeds}, {seed}"

    return list(merged.values())


def remove_blacklisted(keywords, blacklist_terms):
    """제외키워드(브랜드명/금지품목 등)에 걸리는 키워드를 목록에서 제거합니다."""
    if not blacklist_terms:
        return keywords, 0

    kept = []
    removed_count = 0
    for item in keywords:
        blocked, _, _ = blacklist_filter.check_blacklist(item["키워드"], blacklist_terms)
        if blocked:
            removed_count += 1
        else:
            kept.append(item)

    return kept, removed_count


def ask_seed_source(default_csv_path):
    """
    시드 키워드를 어디서 가져올지 물어봅니다.
    1) 지금 키워드를 하나 직접 입력 - seed_keywords.csv를 안 건드리고 그 자리에서 바로 조사
    2) seed_keywords.csv에 적어둔 목록 전체 사용 (기존 방식)

    반환값: 시드 키워드 리스트
    """
    print("\n어떤 키워드를 조사할까요?")
    print("  1. 지금 키워드를 하나 직접 입력할게요")
    print(f"  2. {default_csv_path} 파일에 적어둔 목록 전체를 조사할게요")

    while True:
        try:
            answer = input("  1 또는 2를 입력하세요: ").strip()
        except EOFError:
            print(f"[안내] 입력을 받을 수 없는 환경이라 {default_csv_path} 목록으로 진행합니다.")
            return load_seed_keywords(default_csv_path)

        if answer == "1":
            keyword = input("  조사할 키워드를 입력하세요: ").strip()
            if keyword:
                return [keyword]
            print("  [안내] 키워드가 비어있어요. 다시 입력해주세요.")
            continue

        if answer == "2":
            return load_seed_keywords(default_csv_path)

        print("  [안내] 1 또는 2만 입력할 수 있어요. 다시 입력해주세요.")


def _default_next_months(count=3):
    """오늘 날짜 기준으로 '다음 N개월'을 자동 계산합니다. (예: 7월에 실행하면 8,9,10월)"""
    this_month = datetime.date.today().month
    return [(this_month + i - 1) % 12 + 1 for i in range(1, count + 1)]


def ask_target_months():
    """
    실행할 때마다 몇 월(들) 기준으로 조사할지 물어봅니다.
    쉼표로 여러 달을 한 번에 입력할 수 있습니다 (예: 8,9,10).
    그냥 Enter만 누르면, 오늘 날짜 기준 "다음 3개월"이 자동으로 선택됩니다.
    각 달마다 "작년 이맘때 대비 계절지수"와 "예상 검색량"을 따로 보여줍니다.
    """
    default_months = _default_next_months()
    default_label = ",".join(str(m) for m in default_months)

    print("\n몇 월 기준으로 황금키워드를 찾을까요? (여러 달을 같이 보고 싶으면 쉼표로 구분: 예) 8,9,10)")
    print(f"  그냥 Enter만 누르면 오늘 기준 다음 3개월({default_label}월)로 자동 진행합니다.")

    while True:
        try:
            answer = input("  숫자를 입력하거나 Enter: ").strip()
        except EOFError:
            print(f"[안내] 입력을 받을 수 없는 환경이라 다음 3개월({default_label}월)로 자동 진행합니다.")
            return default_months

        if answer == "":
            print(f"[안내] 다음 3개월({default_label}월) 기준으로 진행합니다.")
            return default_months

        parts = [p.strip() for p in answer.split(",") if p.strip()]
        months = []
        valid = bool(parts)
        for p in parts:
            try:
                month = int(p)
            except ValueError:
                valid = False
                break
            if not (1 <= month <= 12):
                valid = False
                break
            if month not in months:
                months.append(month)

        if valid:
            return months

        print("  [안내] 1~12 사이의 숫자를 쉼표로 구분해서 입력하거나, 그냥 Enter를 누르세요. 다시 입력해주세요.")


def score_and_enrich(keywords, target_months, blacklist_terms):
    """검색량 기준으로 걸러내고, 상위 키워드는 계절성까지 조회해서 점수를 매깁니다."""
    passed = [k for k in keywords if k["월간검색량"] >= config.MIN_MONTHLY_SEARCH_VOLUME]
    passed.sort(key=lambda k: k["월간검색량"], reverse=True)

    datalab_ready = naver_datalab.is_configured()
    if not datalab_ready:
        print("[안내] 데이터랩 API 키가 없어 '계절성' 조회를 건너뜁니다. (검색량/경쟁정도만으로 점수 계산)")

    months_label = ",".join(str(m) for m in target_months)

    # 목표 월이 있으면 "숨은 시즌 키워드"를 놓치지 않기 위해 더 많은 후보를 조회
    seasonality_limit = config.MONTHLY_ANALYSIS_MAX_KEYWORDS
    print(f"[안내] {months_label}월 기준으로 계절성이 강한 키워드를 우선 순위에 둡니다.")
    if len(passed) > seasonality_limit:
        print(f"       (계절성 조회는 검색량 상위 {seasonality_limit}개까지만 진행합니다)")

    results = []
    for i, item in enumerate(passed):
        seasonality = None
        season_status = "-"

        if datalab_ready and i < seasonality_limit:
            seasonality, season_status = naver_datalab.get_seasonality(item["키워드"])
            time.sleep(0.2)

        variation_coefficient = seasonality["변동계수"] if seasonality else None
        monthly_avg = seasonality["월별_평균비율"] if seasonality else None

        score = scorer.calc_keyword_score(
            search_volume=item["월간검색량"],
            competition_level=item["경쟁정도"],
            variation_coefficient=variation_coefficient,
            monthly_avg=monthly_avg,
            target_months=target_months,
        )

        row = {
            "키워드": item["키워드"],
            "원본시드키워드": item["원본시드키워드"],
            "월간검색량": item["월간검색량"],
            "PC검색량": item["PC검색량"],
            "모바일검색량": item["모바일검색량"],
            "경쟁정도": item["경쟁정도"],
            "목표월들": months_label,
            "계절성_변동계수": seasonality["변동계수"] if seasonality else "",
            "계절성_최고월": seasonality["최고월"] if seasonality else "",
            "계절성_최저월": seasonality["최저월"] if seasonality else "",
            "계절성조회상태": season_status,
        }

        # 목표월 각각에 대한 계절지수 + 예상검색량(현재 검색량 x 그 달 계절지수)
        # "_추정"인 이유: 실제 과거 절대 검색량이 아니라, 현재 검색량에 3년 평균 계절 패턴 비율을 곱한 값입니다.
        for month in target_months:
            idx = score["월별계절지수"].get(month)
            row[f"{month}월_계절지수_추정"] = idx if idx is not None else ""
            row[f"{month}월_예상검색량_추정"] = round(item["월간검색량"] * idx) if idx is not None else ""

        row["계절성최적월_추정"] = score["최적월"] if score["최적월"] is not None else ""
        row["계절성최적월지수_추정"] = score["최적월계절지수"] if score["최적월계절지수"] is not None else ""

        row["검색량점수"] = score["검색량점수"]
        row["경쟁정도점수"] = score["경쟁정도점수"]
        row["계절성점수"] = score["계절성점수"]
        row["총점"] = score["총점"]

        # 해외구매대행용 크무비 상품 선별 (관련성/크무비 가능성/브랜드 등 "_추정" 컬럼 추가)
        screening = keyword_screening.screen_keyword(item["원본시드키워드"], item["키워드"], blacklist_terms)
        row.update(screening)

        # 중국 소싱 가능성 추정 (screening의 브랜드 판정을 재사용)
        sourcing = china_sourcing.evaluate(
            item["키워드"], screening["브랜드포함여부"] == "예", screening["감지카테고리_추정"]
        )
        row.update(sourcing)

        # 위험도 자동 분류 (전기/배터리/어린이/의료/식품/인증/파손/설치AS/캐릭터/브랜드)
        risk = risk_classifier.classify_risk(item["키워드"], screening["브랜드포함여부"] == "예")
        row.update(risk)

        # 데이터 신뢰도: 계절성까지 실제로 조회됐는지에 따라 갈림 (검색량은 이 시점에서 항상 실측)
        row["데이터신뢰도_추정"] = (
            "높음(검색량·계절성 모두 실측)"
            if season_status == "조회 성공"
            else "중간(검색량만 실측, 계절성 데이터 없음)"
        )

        # 최종 추천 점수 및 등급 (네이버 데이터 총점 + 적합도 + 소싱가능성 + 위험도 종합)
        final = scorer.calc_final_recommendation(
            naver_score=row["총점"],
            suitability_score=screening["해외구매대행적합도점수_추정"],
            sourcing_score=sourcing["중국소싱가능성점수_추정"],
            risk_grade=risk["종합위험도등급_추정"],
            relevance_score=screening["카테고리관련성점수_추정"],
            is_informational=screening["정보성키워드여부_추정"] == "예",
            is_small=screening["소형저가가능성_추정"] == "예",
            is_brand=screening["브랜드포함여부"] == "예",
            is_low_reliability=season_status != "조회 성공",
        )
        row.update(final)

        results.append(row)

    return results


def save_results(results, output_path, target_months=None):
    """결과를 최종추천점수 높은 순으로 정렬해서 CSV로 저장합니다."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    results_sorted = sorted(results, key=lambda r: r["최종추천점수_종합"], reverse=True)
    if results_sorted:
        fieldnames = list(results_sorted[0].keys())
    else:
        fieldnames = build_result_fieldnames(target_months) if target_months else RESULT_FIELDNAMES

    try:
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(
                "※ 안내: 이 결과는 검색량/경쟁정도/계절성만 본 '키워드 후보'입니다. "
                "크무비(부피 200cm·무게 20kg·가격 20만~100만원) 조건은 아직 검증되지 않았습니다. "
                "실제 상품을 찾아 products_template.csv에 입력한 뒤 main.py로 검증하세요. "
                "'_추정' 표시 컬럼은 키워드 글자만 보고 규칙으로 추정한 값이며, 실제 치수·판매 데이터가 아닙니다.\n"
            )
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results_sorted)
    except PermissionError:
        print(f"\n[오류] '{output_path}' 파일에 저장하지 못했습니다.")
        print("       엑셀 등 다른 프로그램에서 이 파일을 열어두신 것 같아요. 파일을 닫고 다시 실행해주세요.")

    return results_sorted


def main():
    print("=" * 50)
    print("키워드 발굴 프로그램을 시작합니다")
    print("=" * 50)

    if not naver_searchad.is_configured():
        print("\n[안내] 검색광고 API 키가 설정되지 않아 연관키워드를 받아올 수 없습니다.")
        print("       (이 기능은 연관키워드 자체를 그 API로만 조회할 수 있어서, 키가 꼭 필요해요)")
        print("       config.py에 NAVER_SEARCHAD_API_KEY / SECRET_KEY / CUSTOMER_ID를 채운 뒤 다시 실행해주세요.")
        # 빈 결과 파일이라도 만들어서, 프로그램 흐름은 정상적으로 끝내줌 (예전 결과가 안 남도록 xlsx도 같이 갱신)
        os.makedirs(os.path.dirname(config.KEYWORD_FINDER_OUTPUT_PATH), exist_ok=True)
        with open(config.KEYWORD_FINDER_OUTPUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
            f.write(
                "※ 안내: 이 결과는 검색량/경쟁정도/계절성만 본 '키워드 후보'입니다. "
                "크무비(부피 200cm·무게 20kg·가격 20만~100만원) 조건은 아직 검증되지 않았습니다. "
                "실제 상품을 찾아 products_template.csv에 입력한 뒤 main.py로 검증하세요. "
                "'_추정' 표시 컬럼은 키워드 글자만 보고 규칙으로 추정한 값이며, 실제 치수·판매 데이터가 아닙니다.\n"
            )
            f.write(",".join(RESULT_FIELDNAMES) + "\n")
        excel_export.export_candidates_and_excluded(
            [], config.KEYWORD_CANDIDATES_XLSX_PATH, config.KEYWORD_EXCLUDED_XLSX_PATH,
            headers=RESULT_FIELDNAMES,
        )
        return

    seeds = ask_seed_source(config.SEED_KEYWORDS_CSV_PATH)
    if not seeds:
        print("\n처리할 시드 키워드가 없어 프로그램을 종료합니다.")
        return

    target_months = ask_target_months()

    print(f"\n총 {len(seeds)}개의 시드 키워드로 연관키워드를 수집합니다...\n")
    keywords = collect_related_keywords(seeds)

    print(f"\n총 {len(keywords)}개의 (중복 제거된) 연관키워드를 모았습니다.")

    blacklist_terms = blacklist_filter.load_blacklist(config.BLACKLIST_CSV_PATH)

    # 브랜드명 포함 무기류/마약류/화학물질 등 제외키워드.csv의 모든 분류를 완전히 제거합니다.
    keywords, removed_count = remove_blacklisted(keywords, blacklist_terms)
    if blacklist_terms:
        print(f"제외키워드(브랜드명/무기류/마약류 등)에 걸려 {removed_count}개를 제외했습니다.")

    print(f"월간 검색량 {config.MIN_MONTHLY_SEARCH_VOLUME:,} 이상만 남기고 점수를 매깁니다...\n")

    results = score_and_enrich(keywords, target_months, blacklist_terms)
    results_sorted = save_results(results, config.KEYWORD_FINDER_OUTPUT_PATH, target_months=target_months)

    result_fieldnames = build_result_fieldnames(target_months)
    cand_path, excl_path, cand_count, excl_count = excel_export.export_candidates_and_excluded(
        results_sorted, config.KEYWORD_CANDIDATES_XLSX_PATH, config.KEYWORD_EXCLUDED_XLSX_PATH,
        headers=result_fieldnames,
    )

    if not results_sorted:
        print("\n[안내] 조건을 통과한 키워드가 없어서 결과가 비어있습니다.")
        print("       (검색량 기준을 통과한 키워드가 없거나, 전부 브랜드/금지품목으로 제외됐을 수 있어요)")
        print("       그래도 output 폴더의 파일들은 방금 실행 결과(빈 상태)로 새로 갱신했습니다 — 예전 결과가 남아있지 않아요.")

    print("\n" + "=" * 50)
    print("처리 완료!")
    print(f"  - 수집된 연관키워드 수     : {len(keywords)}개")
    print(f"  - 검색량 기준 통과         : {len(results_sorted)}개")
    print(f"  - 전체 결과 파일           : {config.KEYWORD_FINDER_OUTPUT_PATH}")
    print(f"  - 추천 후보 ({cand_count}개)         : {cand_path}")
    print(f"  - 제외/검토 필요 ({excl_count}개)    : {excl_path}")
    print("=" * 50)

    if results_sorted:
        print("\n[상위 황금키워드 후보 미리보기]")
        for i, row in enumerate(results_sorted[:10], start=1):
            best_month = row.get("계절성최적월_추정", "")
            best_label = f", {best_month}월에 가장 강세" if best_month != "" else ""
            print(
                f"  {i}. {row['키워드']} - 추천등급 {row['추천등급']} (최종 {row['최종추천점수_종합']}점) "
                f"[위험도: {row['종합위험도등급_추정']}] (검색량 {row['월간검색량']:,}, 경쟁도 {row['경쟁정도']}{best_label})"
            )

    print("\n※ 안내: 위 키워드들은 아직 크무비(부피/무게/가격) 조건이 검증되지 않았습니다.")
    print("       실제 상품을 찾아 products_template.csv에 입력한 뒤, main.py로 최종 검증하세요.")


if __name__ == "__main__":
    main()
