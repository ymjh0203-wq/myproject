"""검색수 경계값(29/30/999/1000)에서 등급 판정이 설계서 기준대로 나오는지 확인.

pytest 없이 `python tests/test_grading.py`로 실행한다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.wing.search_count_manual import apply_search_count
from app.db import repository

# (검색수, 기대 status, 기대 grade)
CASES = [
    (0, "제외", None),
    (29, "제외", None),
    (30, "저장", "일반 유망상품"),
    (999, "저장", "일반 유망상품"),
    (1000, "저장", "S등급"),
]


def main() -> None:
    repository.init_db()
    test_ids = []
    failures = []

    for i, (search_count, expected_status, expected_grade) in enumerate(CASES):
        coupang_id = f"TESTG{i:03d}"
        test_ids.append(coupang_id)
        row_id, _ = repository.upsert_product_from_search_result(
            coupang_product_id=coupang_id,
            product_name="테스트 상품",
            product_url=f"https://www.coupang.com/vp/products/{coupang_id}",
            price=10000,
        )
        result = apply_search_count(row_id, search_count)
        ok = result["status"] == expected_status and result["grade"] == expected_grade
        mark = "OK" if ok else "FAIL"
        print(
            f"[{mark}] 검색수={search_count} -> status={result['status']}(기대 {expected_status}) "
            f"grade={result['grade']}(기대 {expected_grade})"
        )
        if not ok:
            failures.append(search_count)

    conn = repository.get_connection()
    ids = [
        row["id"]
        for row in conn.execute(
            f"SELECT id FROM products WHERE coupang_product_id IN ({','.join('?' * len(test_ids))})",
            test_ids,
        )
    ]
    if ids:
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM search_count_log WHERE product_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM products WHERE id IN ({placeholders})", ids)
        conn.commit()
    conn.close()

    if failures:
        print(f"\n실패: {failures}")
        raise SystemExit(1)
    print(f"\n전체 {len(CASES)}건 통과")


if __name__ == "__main__":
    main()
