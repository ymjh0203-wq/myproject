"""WING 카탈로그 매칭 검색수 — 수동 보조 모드.

공식 API가 없어(확인됨) 사람이 WING 화면에서 직접 확인한 숫자를 입력받는다.
이 함수는 그 숫자를 받아 등급을 매기고 DB에 반영하는 부분만 담당한다.
브라우저 자동화(자동 모드)는 Phase 5에서 별도로 다룬다.
"""

from datetime import datetime, timezone

from app.core.analyzers.grading import assess_grade
from app.db import repository


def apply_search_count(product_id: int, search_count: int, method: str = "manual") -> dict:
    status, grade, exclusion_reason = assess_grade(search_count)

    repository.update_product_filters(
        product_id,
        search_count=search_count,
        search_count_checked_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        grade=grade,
        exclusion_reason=exclusion_reason,
    )
    repository.log_search_count(product_id, search_count, method=method)

    return {"status": status, "grade": grade, "exclusion_reason": exclusion_reason}
