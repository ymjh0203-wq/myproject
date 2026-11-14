# ==========================================================
# 배대지 원가·배송비 '백그라운드 미리채우기' (services/cost_prefetch_worker.py)
# ----------------------------------------------------------
# 매출정리는 GR마다 퀵스타 배대지를 조회해 단가·배송비를 채웁니다. 그런데 퀵스타 조회 API는
# '분당 10회' 제한이라, 매출정리할 때 한꺼번에 조회하면 느립니다.
#
# 그래서 앱을 켜둔 동안, '출고완료된' 주문들의 원가·배송비를 '미리' 조금씩(한도 안에서)
# 조회해 orders의 캐시 칸에 채워둡니다. 그러면 나중에 매출정리를 열 땐 이미 대부분
# 채워져 있어 '즉시' 끝납니다. (사용자는 기다릴 필요 없음, 수동 다운로드·로그인 없음)
#
# 설계: 데몬 스레드 1개가 '필요한 GR'을 최근 것부터 조금씩 조회→캐시. 조용한 속도(기본
#   건당 ~6초, 분당 ~9건 이하)로 돌고, 큐가 비면 쉬었다 다시 봅니다. 포그라운드 수집이
#   돌 땐 한 박자 쉬어 양보합니다. (전역 쓰로틀이 분당 한도도 함께 지킴)
# ==========================================================

import threading
import time

_thread = None
_lock = threading.Lock()
_stats = {"filled": 0, "last_run": None, "running": False, "remaining_hint": None}

SETTING_KEY = "cost_prefetch_enabled"   # 'off'면 끔. 기본 켜짐.
_GAP_SEC = 6.0        # 건당 최소 간격(조용히)
_EMPTY_SLEEP = 300.0  # 채울 게 없으면 5분 쉬었다 다시 확인
_BATCH = 20


def is_enabled() -> bool:
    try:
        from repositories import settings_repository
        return (settings_repository.get_setting(SETTING_KEY, "on") or "on") != "off"
    except Exception:  # noqa: BLE001
        return True


def get_stats() -> dict:
    return dict(_stats)


def ensure_started() -> None:
    """앱이 떠 있는 동안 1개만 돌도록, 없으면 데몬 스레드를 시작합니다."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_run, name="cost-prefetch", daemon=True)
        _thread.start()


def _run() -> None:
    # 지연 임포트(스레드 안에서): 순환참조·시작시간 영향 최소화
    from integrations import quickstar_client
    from repositories import order_repository
    from services import ledger_match
    from services.ledger_upload_service import _extract_costs

    _stats["running"] = True
    while True:
        try:
            if not is_enabled():
                time.sleep(_EMPTY_SLEEP)
                continue

            # 포그라운드 수집이 돌면 한 박자 쉬어 양보(퀵스타/DB 부담 분산)
            try:
                from services import collect_worker
                if collect_worker.any_running():
                    time.sleep(10.0)
                    continue
            except Exception:  # noqa: BLE001
                pass

            grs = order_repository.list_grs_needing_cost(limit=_BATCH)
            _stats["remaining_hint"] = len(grs)
            if not grs:
                time.sleep(_EMPTY_SLEEP)
                continue

            for gr in grs:
                if not is_enabled():
                    break
                try:
                    data = (quickstar_client.fetch_application(gr) or {}).get("data") or {}  # 전역 쓰로틀 적용됨
                    unit, ship, tcnt, kinds = _extract_costs(data, 1)
                    # 완전할 때(단가+배송비 둘 다)만 저장 → 출고 전 미완성은 다음에 다시.
                    if unit is not None and ship is not None:
                        app_name, app_phone = ledger_match.receiver_of_application(data)
                        order_repository.save_quickstar_cost(
                            gr, unit, ship, app_name, app_phone, kinds, tcnt)
                        _stats["filled"] += 1
                        _stats["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:  # noqa: BLE001
                    # 한 건 실패는 조용히 건너뜀(다음에 다시 시도)
                    pass
                time.sleep(_GAP_SEC)  # 조용한 간격
        except Exception:  # noqa: BLE001
            time.sleep(60.0)
