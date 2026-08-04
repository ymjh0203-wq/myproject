"""Phase 5(자동화/안정화)에서 추가한 repository 함수들의 회귀 테스트.

pytest 없이 `python tests/test_automation_repository.py`로 실행한다.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import repository


def main() -> None:
    repository.init_db()
    failures = []

    def check(label: str, condition: bool) -> None:
        print(f"[{'OK' if condition else 'FAIL'}] {label}")
        if not condition:
            failures.append(label)

    conn = repository.get_connection()

    run_id = repository.start_discovery_run("테스트 전략")
    repository.update_discovery_run_counts(run_id, analyzed_delta=5, found_delta=3, excluded_delta=1)
    repository.finish_discovery_run(run_id, "완료", None)
    row = conn.execute("SELECT * FROM discovery_runs WHERE id = ?", (run_id,)).fetchone()
    check("discovery_runs 카운트 반영", row["analyzed_count"] == 5 and row["found_count"] == 3 and row["excluded_count"] == 1)
    check("discovery_runs 상태 완료", row["status"] == "완료")

    run_id2 = repository.start_discovery_run("중단될 세션")
    repository.close_stale_running_discovery_runs()
    row2 = conn.execute("SELECT * FROM discovery_runs WHERE id = ?", (run_id2,)).fetchone()
    check("진행중 세션이 정리됨", row2["status"] == "중단" and row2["stop_reason"] == "비정상 종료(앱 재시작)")

    repository.promote_word_to_dictionary("테스트단어", "속성어")
    check("사전 학습 반영", repository.lookup_word_type("테스트단어") == "속성어")

    row_id, _ = repository.upsert_product_from_search_result(
        coupang_product_id="TESTQ001",
        product_name="재검사 테스트 상품",
        product_url="https://www.coupang.com/vp/products/TESTQ001",
        price=5000,
    )
    old_date = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    repository.update_product_filters(
        row_id, status="저장", grade="일반 유망상품", search_count=50, search_count_checked_at=old_date
    )
    queue = repository.list_products_awaiting_check()
    check("재검사 대상이 대기열에 포함됨", any(r["id"] == row_id for r in queue))

    check("오늘 분석 수에 방금 만든 테스트 상품 포함", repository.count_products_discovered_today() >= 1)

    ids_to_delete = [
        r["id"] for r in conn.execute("SELECT id FROM products WHERE coupang_product_id LIKE 'TESTQ%'")
    ]
    if ids_to_delete:
        placeholders = ",".join("?" * len(ids_to_delete))
        conn.execute(f"DELETE FROM search_count_log WHERE product_id IN ({placeholders})", ids_to_delete)
        conn.execute(f"DELETE FROM products WHERE id IN ({placeholders})", ids_to_delete)
    conn.execute("DELETE FROM discovery_runs WHERE id IN (?, ?)", (run_id, run_id2))
    conn.execute("DELETE FROM prefix_dictionary WHERE word = '테스트단어'")
    conn.commit()
    conn.close()

    if failures:
        print(f"\n실패: {failures}")
        raise SystemExit(1)
    print("\n전체 통과")


if __name__ == "__main__":
    main()
