# ==========================================================
# 알람(새 주문/취소/반품/CS) 감지 서비스 (services/alarm_service.py)
# ----------------------------------------------------------
# 샵마인처럼 새 건이 들어오면 화면 상단에 배너 + 알림음으로 알려주기 위한
# "새 건수 감지" 로직입니다.
#
# 핵심 아이디어:
#   각 데이터(주문/취소/반품/문의) 테이블에는 우리가 그 건을 '처음 수집한
#   시각'인 first_collected_at 이 들어 있습니다. 사용자가 마지막으로 "확인함"을
#   누른 시각(=기준선, baseline)보다 뒤에 수집된 건 = "아직 못 본 새 건".
#   → 쿠팡 상태코드 해석에 의존하지 않아 안정적이고, "확인함"을 누르면 기준선을
#     지금 시각으로 옮겨 배지가 깔끔히 사라집니다.
#
# 백그라운드 폴러:
#   앱(서버)이 떠 있는 동안 일정 주기(기본 5분)로 쿠팡에서 신규주문/취소/반품/
#   상품문의(+옵션: 콜센터문의)를 조용히 재수집합니다. 그래야 사용자가 '수집하기'를
#   직접 누르지 않아도 새 건이 DB에 들어와 알람이 뜹니다. 실패해도 무시하고
#   다음 주기에 다시 시도합니다.
# ==========================================================

import threading
import time
from datetime import date, datetime, timedelta

import models
from database import get_connection
from repositories import settings_repository

# (key, 화면에 보일 이름, ?view= 로 이동할 화면 이름)
ALARM_TYPES = [
    ("new_orders", "신규주문", "신규주문"),
    ("cancel", "취소요청", "취소주문"),
    ("return", "반품요청", "반품주문"),
    ("cs", "CS문의", "긴급/문의관리"),
]
ALARM_KEYS = [t[0] for t in ALARM_TYPES]
ALARM_LABELS = {t[0]: t[1] for t in ALARM_TYPES}
ALARM_VIEWS = {t[0]: t[2] for t in ALARM_TYPES}

_BASELINE_KEY = "alarm_baseline_at:{}"
_ENABLED_KEY = "alarm_enabled"          # 'on'/'off' (기본 on)
_SOUND_KEY = "alarm_sound_enabled"      # 'on'/'off' (기본 on)
_WIN_TOAST_KEY = "alarm_win_toast_enabled"  # 'on'/'off' (기본 on) — 윈도우 오른쪽아래 데스크톱 알림
_INTERVAL_KEY = "alarm_poll_interval_sec"   # 문자열 숫자 (기본 300초)

_DEFAULT_INTERVAL = 300
_MIN_INTERVAL = 60
_POLL_WINDOW_DAYS = 7   # 폴러가 재수집할 최근 기간

_MARKET_NAME = "coupang"


# ---------------- 설정(켜기/끄기/주기) ----------------

def is_enabled() -> bool:
    return (settings_repository.get_setting(_ENABLED_KEY, "on") or "on").lower() != "off"


def set_enabled(enabled: bool) -> None:
    settings_repository.set_setting(_ENABLED_KEY, "on" if enabled else "off")


def is_sound_enabled() -> bool:
    return (settings_repository.get_setting(_SOUND_KEY, "on") or "on").lower() != "off"


def set_sound_enabled(enabled: bool) -> None:
    settings_repository.set_setting(_SOUND_KEY, "on" if enabled else "off")


def is_win_toast_enabled() -> bool:
    return (settings_repository.get_setting(_WIN_TOAST_KEY, "on") or "on").lower() != "off"


def set_win_toast_enabled(enabled: bool) -> None:
    settings_repository.set_setting(_WIN_TOAST_KEY, "on" if enabled else "off")


def get_poll_interval() -> int:
    raw = settings_repository.get_setting(_INTERVAL_KEY)
    try:
        return max(_MIN_INTERVAL, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL


def set_poll_interval(seconds: int) -> None:
    settings_repository.set_setting(_INTERVAL_KEY, str(max(_MIN_INTERVAL, int(seconds))))


# ---------------- 기준선(baseline) ----------------

def _now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_baseline(key: str) -> str:
    """해당 종류의 '마지막 확인 시각'. 처음이면 지금으로 설정하고 돌려줍니다
    (=설치 이전의 과거 건들이 한꺼번에 새 건으로 뜨는 것을 막음)."""
    value = settings_repository.get_setting(_BASELINE_KEY.format(key))
    if not value:
        value = _now_str()
        settings_repository.set_setting(_BASELINE_KEY.format(key), value)
    return value


def acknowledge(keys=None) -> None:
    """'확인함' — 지정한 종류(없으면 전체)의 기준선을 지금 시각으로 옮겨 배지를 지웁니다."""
    now = _now_str()
    target = ALARM_KEYS if keys is None else keys
    for key in target:
        if key in ALARM_KEYS:
            settings_repository.set_setting(_BASELINE_KEY.format(key), now)


# ---------------- 새 건수 세기 ----------------

def _count(sql: str, params: tuple) -> int:
    connection = get_connection()
    try:
        row = connection.execute(sql, params).fetchone()
        return int(row[0]) if row else 0
    finally:
        connection.close()


def _count_for_key(key: str, base: str) -> int:
    """지정한 종류의 'base 시각 이후 새로 수집된 건수'를 셉니다. (배너·토스트가 공용으로 사용)"""
    if key == "new_orders":
        # 신규주문: 아직 신규 단계이면서, base 이후 처음 수집된 주문
        return _count(
            "SELECT COUNT(*) FROM orders WHERE work_status = ? AND first_collected_at > ?",
            (models.WORK_STATUS_NEW, base),
        )
    if key == "return":
        # 반품요청 = RETURN 중 '완료 아님' + '출고중지(RELEASE_STOP) 아님'
        return _count(
            "SELECT COUNT(*) FROM claims WHERE claim_type = 'RETURN' AND first_collected_at > ? "
            "AND receipt_status != 'RETURNS_COMPLETED' AND receipt_status NOT LIKE 'RELEASE_STOP%'",
            (base,),
        )
    if key == "cancel":
        # 취소요청 = CANCEL(완료 아님) + RETURN이지만 출고중지(RELEASE_STOP=실제 취소)인 건
        return _count(
            "SELECT COUNT(*) FROM claims WHERE first_collected_at > ? AND receipt_status != 'RETURNS_COMPLETED' "
            "AND (claim_type = 'CANCEL' OR (claim_type = 'RETURN' AND receipt_status LIKE 'RELEASE_STOP%'))",
            (base,),
        )
    if key == "cs":
        # CS문의: 상품문의(미답변) + 콜센터문의, base 이후 새로 수집된 건
        return _count(
            "SELECT COUNT(*) FROM product_inquiries "
            "WHERE first_collected_at > ? AND (answered IS NULL OR answered = 0)",
            (base,),
        ) + _count(
            "SELECT COUNT(*) FROM call_center_inquiries WHERE first_collected_at > ?",
            (base,),
        )
    return 0


def count_unseen() -> dict:
    """종류별로 '마지막 확인 이후 새로 수집된 건수'를 돌려줍니다."""
    return {key: _count_for_key(key, get_baseline(key)) for key in ALARM_KEYS}


# ---------------- 윈도우 데스크톱 알림(토스트) ----------------

def notify_windows_toast(title: str, body: str, sound: bool = True) -> None:
    """윈도우 오른쪽 아래에 데스크톱 알림(토스트)을 소리와 함께 띄웁니다(샵마인처럼).
    추가 설치 없이 Windows 기본 기능(WinRT)만 씁니다. 실패해도 조용히 넘어갑니다.
    title=종류 이름(예: '신규주문'), body=내용(예: '2건 들어왔습니다')."""
    import subprocess

    def xesc(text: str) -> str:
        # XML 특수문자 이스케이프( '는 &apos; 로 → PowerShell 작은따옴표 문자열과 충돌 방지 )
        return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&apos;"))

    audio = ('<audio src="ms-winsoundevent:Notification.Default" loop="false"/>'
             if sound else '<audio silent="true"/>')
    xml = (
        '<toast><visual><binding template="ToastText02">'
        f'<text id="1">{xesc(title)}</text>'
        f'<text id="2">{xesc(body)}</text>'
        '</binding></visual>'
        f'{audio}</toast>'
    )
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]>$null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]>$null;"
        "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml('{xml}');"
        "$toast=[Windows.UI.Notifications.ToastNotification]::new($x);"
        "$aid='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aid).Show($toast);"
    )
    try:
        creationflags = 0x08000000  # CREATE_NO_WINDOW (검은 창 안 뜨게)
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            creationflags=creationflags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ---------------- 백그라운드 폴러 ----------------

_poller_thread = None
_poller_lock = threading.Lock()

# ★토스트 중복 방지 기준시각을 'DB'에 저장합니다(종류별). 예전엔 메모리 변수(_last_counts)에만
#   기록해서, 앱을 재시작하거나 서버가 중복 실행되면 같은 건에 토스트가 다시 떴습니다.
#   DB에 두면 재시작·중복에도 '이미 알린 시각' 이후 새 건만 한 번 알립니다.
_TOAST_BASE_KEY = "alarm_toast_at:{}"


def _maybe_toast_new_arrivals() -> None:
    """DB에 저장된 '마지막으로 토스트한 시각' 이후 새로 들어온 건만 한 번 윈도우 토스트로 알립니다."""
    now = _now_str()
    for key in ALARM_KEYS:
        base_key = _TOAST_BASE_KEY.format(key)
        base = settings_repository.get_setting(base_key)
        if not base:
            # 최초 1회: 기준시각만 잡고 넘어감(기존 밀린 건으로는 토스트 안 함).
            settings_repository.set_setting(base_key, now)
            continue
        delta = _count_for_key(key, base)
        if delta > 0 and is_win_toast_enabled():
            # 종류별로 각각 알림 → 제목이 '신규주문'/'반품요청'처럼 종류 그대로 보입니다.
            notify_windows_toast(ALARM_LABELS[key], f"{delta}건 들어왔습니다", sound=True)
        # 알렸든 아니든 기준시각을 지금으로 옮겨, 이 건들이 다음에 또 안 울리게 합니다.
        settings_repository.set_setting(base_key, now)


def run_sync_once() -> None:
    """쿠팡에서 신규주문/취소/반품/문의를 한 번 조용히 재수집합니다.
    각 항목은 독립적으로 try/except 처리 — 하나가 실패해도 나머지는 진행합니다."""
    # 무거운 import를 폴러 안에서(앱 로딩 지연 방지)
    from services import claims_sync_service, sync_service

    period_to = date.today()
    period_from = period_to - timedelta(days=_POLL_WINDOW_DAYS)

    # 콜센터문의는 계정에 따라 500 오류가 나서, 사용자가 설정에서 켠 경우에만 시도
    try:
        from ui import settings as ui_settings
        collect_call_center = ui_settings.is_call_center_sync_enabled()
    except Exception:
        collect_call_center = False

    jobs = [
        lambda: sync_service.sync_new_orders(period_from, period_to),
        lambda: claims_sync_service.sync_claims("CANCEL", period_from, period_to),
        lambda: claims_sync_service.sync_claims("RETURN", period_from, period_to),
        lambda: claims_sync_service.sync_product_inquiries(period_from, period_to),
    ]
    if collect_call_center:
        jobs.append(lambda: claims_sync_service.sync_call_center_inquiries(period_from, period_to))

    for job in jobs:
        try:
            job()
        except Exception:
            # 폴러는 조용히 실패를 삼킵니다(다음 주기에 재시도).
            pass

    settings_repository.set_setting("alarm_last_poll_at", _now_str())


_INIT_FLAG = "alarm_baseline_initialized"


def _poller_loop() -> None:
    # 앱이 막 뜬 직후에는 자동수집(app.py)이 돌고 있을 수 있으니 살짝 뒤에 시작.
    time.sleep(20)
    while True:
        try:
            if is_enabled():
                run_sync_once()
                # 프로그램을 처음 쓸 때(기준선 최초 설정 전): 첫 수집으로 들어온 과거 누적분은
                # '이미 본 것'으로 처리 → 이후 '진짜 새로 들어오는' 건만 알람이 울립니다.
                if not settings_repository.get_setting(_INIT_FLAG):
                    acknowledge()
                    settings_repository.set_setting(_INIT_FLAG, "on")
                    # 토스트 기준시각도 지금으로 잡아, 기존 밀린 건으로는 토스트하지 않습니다.
                    for key in ALARM_KEYS:
                        settings_repository.set_setting(_TOAST_BASE_KEY.format(key), _now_str())
                else:
                    # 마지막으로 알린 시각 이후 새로 들어온 건만 한 번 윈도우 토스트로 알립니다.
                    _maybe_toast_new_arrivals()
        except Exception:
            pass
        time.sleep(get_poll_interval())


def ensure_poller_started() -> None:
    """폴러 스레드를 딱 한 번만 띄웁니다(Streamlit 재실행에도 중복 생성 안 함)."""
    global _poller_thread
    with _poller_lock:
        if _poller_thread is not None and _poller_thread.is_alive():
            return
        _poller_thread = threading.Thread(target=_poller_loop, name="alarm-poller", daemon=True)
        _poller_thread.start()


def get_last_poll_at() -> str:
    return settings_repository.get_setting("alarm_last_poll_at")
