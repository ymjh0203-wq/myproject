# ==========================================================
# 백그라운드 수집 워커 (services/collect_worker.py)
# ----------------------------------------------------------
# '수집하기'를 누르면 주문 최신화가 Streamlit 스크립트 안에서 '동기'로 돌면,
# 도중에 다른 메뉴를 클릭할 때 Streamlit이 스크립트를 취소해 수집이 끊깁니다.
# 그래서 실제 수집은 여기 '별도 스레드'에서 돌립니다(화면을 옮겨도 끝까지 진행).
#
# ⭐ 단계별 동시 수집: 상태를 '버튼 키별'로 따로 관리합니다. 그래서 발송대기에서
#    수집하는 중에도 신규주문·배송중에서 각각 수집을 동시에 누를 수 있습니다.
#    각 수집은 '그 단계'만 최신화합니다(신규=결제완료, 발송대기=상품준비중 등).
#    신규주문·발송대기 수집에는 취소·반품 정리(reconcile)도 함께 돕니다.
#
# 주의: 이 스레드에서는 st.* 를 절대 호출하지 않습니다(스크립트 컨텍스트 없음).
# DB는 get_connection()이 호출마다 새 연결을 열어 스레드-안전합니다.
# ==========================================================

import threading
from datetime import datetime

from services import sync_service

_lock = threading.Lock()
# 버튼 키(new_orders / rts / shipping / delivered ...) -> 진행상태
_states: dict = {}


def _blank_state() -> dict:
    return {
        "status": "idle",        # idle | running | done
        "result": None,
        "error": None,
        "started_at": None,
        "finished_at": None,
        "progress": 0.0,
        "progress_text": "",
        "period": None,
    }


def get_state(key: str) -> dict:
    with _lock:
        return dict(_states.get(key) or _blank_state())


def is_running(key: str) -> bool:
    with _lock:
        s = _states.get(key)
        return bool(s and s["status"] == "running")


def any_running() -> bool:
    with _lock:
        return any(s["status"] == "running" for s in _states.values())


def start(key: str, stages: list, period_from, period_to, reconcile: bool = False,
          advance_delivered: bool = False, advance_confirmed: bool = False) -> bool:
    """
    이 키(단계)의 수집을 백그라운드로 시작합니다. 같은 키가 이미 돌고 있으면 False.
    다른 키는 서로 막지 않아, 여러 단계를 동시에 수집할 수 있습니다.
    advance_delivered=True면 마지막에 '배송완료된 배송중 주문 정리'(전진)를 함께 합니다.
    advance_confirmed=True면 '주문 후 오래된 배송완료 주문 → 구매확정' 전진을 함께 합니다.
    """
    with _lock:
        s = _states.get(key)
        if s and s["status"] == "running":
            return False
        _states[key] = {
            "status": "running",
            "result": None,
            "error": None,
            "started_at": datetime.now().strftime("%H:%M:%S"),
            "finished_at": None,
            "progress": 0.0,
            "progress_text": "수집 준비 중…",
            "period": (str(period_from), str(period_to)),
        }
    thread = threading.Thread(
        target=_run,
        args=(key, list(stages), period_from, period_to, reconcile, advance_delivered,
              advance_confirmed),
        daemon=True,
    )
    thread.start()
    return True


def clear(key: str) -> None:
    """완료(done) 상태를 idle로 돌려, 결과 배너를 한 번만 보여주게 합니다."""
    with _lock:
        s = _states.get(key)
        if s and s["status"] == "done":
            s.update({"status": "idle", "result": None, "error": None,
                      "progress": 0.0, "progress_text": ""})


def _set_progress(key: str, done: int, total: int, name: str) -> None:
    with _lock:
        s = _states.get(key)
        if not s:
            return
        s["progress"] = (done / total) if total else 0.0
        pct = int(done / total * 100) if total else 0
        tail = f" [{name}]" if name else ""
        s["progress_text"] = f"주문 최신화 중… {done}/{total} ({pct}%){tail}"


def _finish(key: str, result: dict, error: str = None) -> None:
    with _lock:
        s = _states.get(key)
        if not s:
            s = _blank_state()
            _states[key] = s
        s.update({
            "status": "done",
            "result": result,
            "error": error,
            "progress": 1.0,
            "progress_text": "수집 완료",
            "finished_at": datetime.now().strftime("%H:%M:%S"),
        })


def _run(key: str, stages: list, period_from, period_to, reconcile: bool,
         advance_delivered: bool = False, advance_confirmed: bool = False) -> None:
    """
    지정한 단계(stages)만 순서대로 최신화합니다. sync_orders_for_stage가 단계별
    실시간 건수('쿠팡 기준 현재 N건')도 각자 저장하므로 배너도 함께 갱신됩니다.
    reconcile=True면 마지막에 취소·반품 정리(사라진 신규/발송대기 주문 정리)를 합니다.
    advance_delivered=True면 마지막에 배송완료된 배송중 주문을 배송완료로 전진시킵니다.
    advance_confirmed=True면 주문 후 오래된 배송완료 주문을 구매확정으로 전진시킵니다.
    """
    try:
        total = (len(stages) + (1 if reconcile else 0) + (1 if advance_delivered else 0)
                 + (1 if advance_confirmed else 0))
        done = 0
        new_c = updated_c = fetched_c = closed_c = advanced_c = 0
        errors = []
        _set_progress(key, 0, total, "")

        for stage in stages:
            try:
                r = sync_service.sync_orders_for_stage(stage, period_from, period_to)
                new_c += r.get("new_count", 0) or 0
                updated_c += r.get("updated_count", 0) or 0
                fetched_c += r.get("fetched_count", 0) or 0
                if r.get("error_message"):
                    errors.append(f"[{stage}] {r['error_message']}")
            except Exception as error:  # noqa: BLE001
                errors.append(f"[{stage}] {error}")
            done += 1
            _set_progress(key, done, total, stage)

        if reconcile:
            try:
                rc = sync_service.reconcile_active_orders(period_from, period_to)
                closed_c += rc.get("closed_count", 0) or 0
                advanced_c += rc.get("advanced_count", 0) or 0
                if rc.get("error_message"):
                    errors.append(rc["error_message"])
            except Exception as error:  # noqa: BLE001
                errors.append(str(error))
            done += 1
            _set_progress(key, done, total, "취소·반품 정리")

        if advance_delivered:
            try:
                ad = sync_service.advance_delivered_orders()
                advanced_c += ad.get("advanced_count", 0) or 0
                if ad.get("error_message"):
                    errors.append(ad["error_message"])
            except Exception as error:  # noqa: BLE001
                errors.append(str(error))
            done += 1
            _set_progress(key, done, total, "배송완료된 주문 정리")

        if advance_confirmed:
            try:
                ac = sync_service.advance_confirmed_orders()
                advanced_c += ac.get("advanced_count", 0) or 0
                if ac.get("error_message"):
                    errors.append(ac["error_message"])
            except Exception as error:  # noqa: BLE001
                errors.append(str(error))
            done += 1
            _set_progress(key, done, total, "구매확정 전진")

        if not errors:
            status = "success"
        elif fetched_c > 0 or new_c + updated_c + closed_c + advanced_c > 0:
            status = "partial"
        else:
            status = "fail"

        _finish(key, {
            "status": status,
            "fetched_count": fetched_c,
            "new_count": new_c,
            "updated_count": updated_c,
            "closed_count": closed_c,
            "advanced_count": advanced_c,
            "error_message": " / ".join(errors[:6]) if errors else None,
        })
    except Exception as error:  # noqa: BLE001
        _finish(
            key,
            {"status": "fail", "fetched_count": 0, "new_count": 0, "updated_count": 0,
             "closed_count": 0, "error_message": str(error)},
            error=str(error),
        )
