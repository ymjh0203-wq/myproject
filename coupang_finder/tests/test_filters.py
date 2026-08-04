"""설계서 6장의 브랜드/효자상품 판별 예시가 그대로 통과하는지 확인하는 회귀 테스트.

pytest 없이 그냥 `python tests/test_filters.py`로 실행한다. 테스트용으로 넣은
상품 데이터(coupang_product_id가 'TEST'로 시작)는 끝나면 스스로 지운다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.analyzers.pipeline import run_filters
from app.db import repository

# (상품명, 판매자명, 기대 brand_risk_level, 기대 hyoja_status)
CASES = [
    ("가르본 차양막 주차장 천막", "가르본상점", "안전", "효자상품 의심"),
    ("삼성 이동식 에어컨", "홈가전마트", "제외", "정상"),
    ("접이식 캠핑 테이블", "캠핑나라", "안전", "정상"),
    ("대형 야외 주차장 천막", "천막공장", "안전", "정상"),
    ("나이키 운동화", "스포츠샵", "제외", "정상"),
    ("다이슨 호환 거치대", "홈리빙", "주의", "정상"),
    ("무브랜드 수납선반", "수납전문점", "안전", "정상"),
]


def main() -> None:
    repository.init_db()
    test_ids = []
    failures = []

    for i, (name, seller, expected_brand, expected_hyoja) in enumerate(CASES):
        coupang_id = f"TEST{i:03d}"
        test_ids.append(coupang_id)
        row_id, _ = repository.upsert_product_from_search_result(
            coupang_product_id=coupang_id,
            product_name=name,
            product_url=f"https://www.coupang.com/vp/products/{coupang_id}",
            price=10000,
        )
        repository.update_product_filters(row_id, seller_name=seller)
        result = run_filters(row_id)

        ok = result["brand_risk_level"] == expected_brand and result["hyoja_status"] == expected_hyoja
        mark = "OK" if ok else "FAIL"
        print(
            f"[{mark}] {name} — 브랜드={result['brand_risk_level']}(기대 {expected_brand}) "
            f"효자상품={result['hyoja_status']}(기대 {expected_hyoja})"
        )
        if not ok:
            failures.append(name)

    conn = repository.get_connection()
    conn.execute(
        f"DELETE FROM products WHERE coupang_product_id IN ({','.join('?' * len(test_ids))})",
        test_ids,
    )
    conn.commit()
    conn.close()

    if failures:
        print(f"\n실패: {len(failures)}건 — {failures}")
        raise SystemExit(1)
    print(f"\n전체 {len(CASES)}건 통과")


if __name__ == "__main__":
    main()
