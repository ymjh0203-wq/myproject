"""search_rank가 실제로 저장되고, 검색수 확인 대기열이 우선순위(랭킹) 순으로
정렬되는지 확인하는 회귀 테스트.

pytest 없이 `python tests/test_search_priority.py`로 실행한다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.analyzers.search_priority import estimate_priority
from app.db import repository


def main() -> None:
    repository.init_db()
    failures = []

    def check(label: str, condition: bool) -> None:
        print(f"[{'OK' if condition else 'FAIL'}] {label}")
        if not condition:
            failures.append(label)

    check("랭킹 1위 우선순위가 랭킹 40위보다 높음", estimate_priority(1) > estimate_priority(40))
    check("랭킹 없음(None)은 중립값 50", estimate_priority(None) == 50)

    test_ids = []
    # 랭킹 30위 상품을 먼저 넣고, 랭킹 2위 상품을 나중에 넣어도 정렬은 랭킹 2위가 위로 와야 한다
    row_id_low_rank, _ = repository.upsert_product_from_search_result(
        coupang_product_id="TESTR001",
        product_name="랭킹 30위 테스트 상품",
        product_url="https://www.coupang.com/vp/products/TESTR001",
        price=10000,
        search_rank=30,
    )
    row_id_high_rank, _ = repository.upsert_product_from_search_result(
        coupang_product_id="TESTR002",
        product_name="랭킹 2위 테스트 상품",
        product_url="https://www.coupang.com/vp/products/TESTR002",
        price=10000,
        search_rank=2,
    )
    test_ids += ["TESTR001", "TESTR002"]
    # list_products_awaiting_check()는 status='검토중'(또는 재검사 지난 저장)만 대상으로 하므로 맞춰준다
    repository.update_product_filters(row_id_low_rank, status="검토중")
    repository.update_product_filters(row_id_high_rank, status="검토중")

    conn = repository.get_connection()
    row = conn.execute("SELECT search_rank FROM products WHERE id = ?", (row_id_high_rank,)).fetchone()
    check("search_rank이 실제로 저장됨", row["search_rank"] == 2)

    rows = repository.list_products_awaiting_check()
    rows = sorted(rows, key=lambda r: estimate_priority(r["search_rank"]), reverse=True)
    test_rows = [r for r in rows if r["coupang_product_id"] in ("TESTR001", "TESTR002")]
    check(
        "랭킹 2위 상품이 랭킹 30위 상품보다 정렬 순서상 앞에 옴",
        test_rows and test_rows[0]["coupang_product_id"] == "TESTR002",
    )

    ids_to_delete = [
        r["id"] for r in conn.execute(
            f"SELECT id FROM products WHERE coupang_product_id IN ({','.join('?' * len(test_ids))})",
            test_ids,
        )
    ]
    if ids_to_delete:
        placeholders = ",".join("?" * len(ids_to_delete))
        conn.execute(f"DELETE FROM search_count_log WHERE product_id IN ({placeholders})", ids_to_delete)
        conn.execute(f"DELETE FROM products WHERE id IN ({placeholders})", ids_to_delete)
        conn.commit()
    conn.close()

    if failures:
        print(f"\n실패: {failures}")
        raise SystemExit(1)
    print("\n전체 통과")


if __name__ == "__main__":
    main()
