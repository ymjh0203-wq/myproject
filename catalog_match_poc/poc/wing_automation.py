"""쿠팡 WING 상품등록 화면을 실제로 채우는 브라우저 자동화.

자동입력 전용 Chrome 프로필을 사용한다. 최종 임시저장/상품등록은 별도
``submit_prepared_job`` 호출에서 실행한다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

from PIL import Image

from .chrome_launcher import find_chrome
from .detail_translation import apply_watermark_to_images
from .registration_plan import (
    DEFAULT_SHIPPING_LEAD_DAYS,
    DEFAULT_STOCK_QUANTITY,
    RETURN_FEE_FALLBACK_CAP,
    build_return_shipping_fee_policy,
    extract_return_fee_allowed_maximum,
    maximum_return_fee_at_or_below,
)
from .seller_page_filter import filter_known_seller_pages
from .url_parser import parse_coupang_url


WING_REGISTRATION_URL = (
    "https://wing.coupang.com/tenants/seller-web/vendor-inventory/formV2"
)
CATALOG_PLACEHOLDER = "상품명, 상품 ID, URL, 브랜드명, 쿠팡 상품 번호"
CATALOG_MODAL_PLACEHOLDER = "상품명, 브랜드명, 상품 URL, 쿠팡상품 번호"
NEW_LISTING_PLACEHOLDER = "상품명, 브랜드, 쿠팡 상품ID, 상품 키워드, URL로 검색"
DEFAULT_DEBUG_PORT = 9223
LOGIN_WAIT_SECONDS = 600
FINAL_SUBMISSION_WAIT_SECONDS = 90
# WING 화면상 한도는 10장이지만 CDN 업로드가 몰리면 묶음 전체가 0장으로
# 멈추는 경우가 있다. 5장씩 보내 안정성을 우선한다.
MAX_DETAIL_UPLOAD_FILES = 5
DETAIL_PANEL_WIDTH = 780
DETAIL_PANEL_MAX_HEIGHT = 4_800
# 쿠팡은 짧은 시간에 상품 상세를 연속 조회하면 정상 브라우저에도
# '사용권한 없음/Access Denied'를 반환한다. 작업 프로세스가 상품마다 새로
# 실행되므로 공용 상태 파일로 조회 간격과 차단 휴식시간을 공유한다.
SOURCE_ACCESS_MIN_INTERVAL_SECONDS = 30
SOURCE_ACCESS_BLOCK_COOLDOWN_SECONDS = 300
SOURCE_ACCESS_BURST_LIMIT = 4
SOURCE_ACCESS_BURST_COOLDOWN_SECONDS = 180

ProgressCallback = Callable[[str, str], None]


class WingAutomationError(RuntimeError):
    """자동입력을 안전하게 계속할 수 없을 때 발생한다."""


class _DeferredOptionMatching(WingAutomationError):
    """임시 상품 ID를 만든 뒤 필수 옵션 연결을 다시 해야 함을 알린다."""


def parse_winner_price(text: str) -> int | None:
    """옵션 카드/행 문구에서 아이템위너 가격을 읽는다."""

    normalized = " ".join((text or "").split())
    patterns = (
        r"아이템\s*위너(?:\s*(?:가격|판매가)|가)?\s*[:：]?\s*"
        r"[₩￦]?\s*([0-9][0-9,]*)(?:\s*원)?",
        r"아이템위너가격.*?([0-9][0-9,]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def _parse_option_row_winner_price(text: str) -> int | None:
    """가상 옵션 행의 아이템위너 가격 셀에서 금액을 읽는다."""

    normalized = " ".join((text or "").split())
    match = re.search(r"([0-9][0-9,]*)\s*\(", normalized)
    if not match:
        match = re.search(r"([0-9][0-9,]*)", normalized)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _normalize_option_identity(value: str) -> str:
    """WING 옵션표와 카탈로그 모달의 옵션명을 안전하게 비교한다."""

    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def _canonical_option_parts(value: str) -> tuple[str, ...]:
    """옵션 속성 순서가 바뀌어도 같은 조합으로 비교한다."""

    parts = [
        _normalize_option_identity(part)
        for part in re.split(r"\s*[,|]\s*", str(value or ""))
    ]
    return tuple(sorted(part for part in parts if part))


def _standard_size_values(values: Sequence[str]) -> list[str]:
    """옵션명에서 의류/잡화에 쓰이는 실제 사이즈만 순서대로 추출한다."""

    result: list[str] = []
    alpha_pattern = re.compile(
        r"(?<![0-9A-Z])(?:\d{1,2}XL|X{1,4}[SL]|[SML]|FREE|F)(?![0-9A-Z])",
        re.IGNORECASE,
    )
    numeric_pattern = re.compile(
        r"(?<!\d)(?:44|55|66|77|88|90|95|100|105|110|115|120)(?:\s*호)?(?!\d)"
    )
    korean_pattern = re.compile(r"(?:프리\s*사이즈|프리|소형|중형|대형)")

    for raw in values:
        text = str(raw or "").strip()
        for match in alpha_pattern.finditer(text.upper()):
            size = match.group(0).upper().replace(" ", "")
            if size not in result:
                result.append(size)
        for match in numeric_pattern.finditer(text):
            size = re.sub(r"\s+", "", match.group(0))
            if size not in result:
                result.append(size)
        for match in korean_pattern.finditer(text):
            size = re.sub(r"\s+", "", match.group(0))
            if size not in result:
                result.append(size)
    return result


def _matched_option_record(option_name: str, matched: Sequence[dict]) -> dict | None:
    """옵션명이 정확히 대응되는 카탈로그 기록을 하나만 반환한다."""

    target = _normalize_option_identity(option_name)
    if not target:
        return None
    exact = [
        item
        for item in matched
        if (
            _normalize_option_identity(str(item.get("name") or "")) == target
            or _canonical_option_parts(str(item.get("name") or ""))
            == _canonical_option_parts(option_name)
        )
    ]
    candidates = exact
    if not candidates:
        candidates = [
            item
            for item in matched
            if (
                target in _normalize_option_identity(str(item.get("name") or ""))
                or _normalize_option_identity(str(item.get("name") or "")) in target
            )
        ]
    if not candidates:
        # 새 WING은 필수 속성(두께·길이 등)을 별도 칸으로 옮기면서 옵션명
        # 표시에서 그 속성만 생략한다. 나머지 쉼표 단위 속성이 모두 같으면
        # 같은 옵션으로 보되, 후보가 하나일 때만 선택해 오매칭을 막는다.
        target_parts = set(_canonical_option_parts(option_name))
        candidates = [
            item
            for item in matched
            if target_parts
            and target_parts.issubset(
                set(_canonical_option_parts(str(item.get("name") or "")))
            )
        ]
    return candidates[0] if len(candidates) == 1 else None


def _matched_option_winner_price(option_name: str, matched: Sequence[dict]) -> int | None:
    """옵션표의 위너가 셀이 비어 있을 때 앞서 읽은 카탈로그 가격을 사용한다.

    새 WING의 가상 스크롤 표는 스크롤 경계에서 ``.pre-matching`` 셀을
    늦게 렌더링하기도 한다. 옵션명이 유일하게 일치할 때만 저장해 둔
    아이템위너 가격을 사용하여 다른 옵션 가격이 섞이지 않게 한다.
    """

    record = _matched_option_record(option_name, matched)
    if record is None:
        return None
    return int(record["winner_price"]) if record.get("winner_price") else None


def _required_option_row_name(row_text: str) -> str:
    """새 WING 필수 옵션 행에서 원래 카탈로그 옵션명만 복원한다."""

    parts = [
        part.strip()
        for part in str(row_text or "").replace("수정", "").split(",")
        if part.strip()
    ]
    # 저장된 행은 ``원본 옵션들, 연결한 필수값`` 순서로 표시된다. 현재
    # 자동 연결값은 첫 번째 원본 값이므로 마지막 중복값만 제거한다.
    if len(parts) >= 3 and parts[-1] == parts[0]:
        parts = parts[:-1]
    return ", ".join(parts)


def _parse_option_total(text: str) -> int:
    match = re.search(
        r"(?:옵션\s*목록\s*\(\s*)?총\s*([0-9,]+)(?:\s*개)?",
        text or "",
    )
    return int(match.group(1).replace(",", "")) if match else 0


def _is_select_all_catalog_option(name: str) -> bool:
    """옵션 모달의 전체선택 행을 실제 판매옵션으로 세지 않는다."""

    return re.sub(r"\s+", "", name or "") in {"전체선택", "모두선택"}


def _catalog_other_seller_count(text: str) -> int | None:
    """카탈로그 옵션 카드의 '다른 판매자(n)' 수를 읽는다."""

    normalized = " ".join((text or "").split())
    match = re.search(
        r"(?:이\s*상품의\s*)?다른\s*판매자\s*\(\s*([0-9][0-9,]*)\s*\)",
        normalized,
        re.IGNORECASE,
    )
    return int(match.group(1).replace(",", "")) if match else None


def _catalog_option_name(text: str, fallback: str) -> str:
    normalized = " ".join((text or "").split())
    match = re.search(r"옵션명\s*(?:\d+\.\s*)?(.+?)\s*상품번호", normalized)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return fallback


def _new_wing_identifier(search_value: str) -> str:
    value = str(search_value or "").strip()
    if value.isdigit():
        return value
    parsed = parse_coupang_url(value)
    return parsed.value_for("productId") or ""


def _source_detail_url_candidates(source_url: str) -> list[str]:
    """상세페이지 차단/지연 때 다시 열어 볼 안전한 쿠팡 URL 목록을 만든다."""

    value = str(source_url or "").strip()
    parsed = parse_coupang_url(value)
    product_id = parsed.value_for("productId")
    candidates: list[str] = []

    def add(candidate: str) -> None:
        candidate = str(candidate or "").strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    add(value)
    if not product_id:
        return candidates

    query = parse_qs(urlparse(value).query)
    retained: list[str] = []
    for key in ("itemId", "vendorItemId"):
        raw = next((item for item in query.get(key, []) if str(item).isdigit()), "")
        if raw:
            retained.append(f"{key}={quote(str(raw))}")
    canonical = f"https://www.coupang.com/vp/products/{product_id}"
    add(canonical + ("?" + "&".join(retained) if retained else ""))
    add(canonical)
    return candidates


def _source_page_block_reason(body_text: str) -> str:
    """쿠팡 상품페이지 대신 차단/오류 화면이 열린 경우를 구분한다."""

    normalized = " ".join(str(body_text or "").split()).lower()
    markers = (
        ("access denied", "접근 차단 화면"),
        ("사용권한이 없습니다", "상품페이지 사용권한 제한 화면"),
        ("사용권한이 제한된", "상품페이지 사용권한 제한 화면"),
        ("비정상적인 접근", "비정상 접근 차단 화면"),
        ("요청이 차단", "요청 차단 화면"),
        ("captcha", "자동접속 확인 화면"),
        ("로봇이 아닙니다", "자동접속 확인 화면"),
        ("잠시 후 다시", "일시적인 오류 화면"),
        ("서비스 이용에 불편", "일시적인 오류 화면"),
    )
    return next((reason for marker, reason in markers if marker in normalized), "")


def _source_access_state_path(output_dir: Path) -> Path:
    return output_dir.parent.parent / ".source_access_state.json"


def _read_source_access_state(output_dir: Path) -> dict:
    path = _source_access_state_path(output_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_source_access_state(output_dir: Path, state: dict) -> None:
    path = _source_access_state_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _wait_for_source_access_slot(output_dir: Path) -> None:
    state = _read_source_access_state(output_dir)
    now = time.time()
    next_allowed = max(
        float(state.get("next_allowed") or 0),
        float(state.get("last_attempt") or 0) + SOURCE_ACCESS_MIN_INTERVAL_SECONDS,
    )
    if next_allowed > now:
        time.sleep(next_allowed - now)
    state["last_attempt"] = time.time()
    state["next_allowed"] = 0
    _write_source_access_state(output_dir, state)


def _record_source_access_block(output_dir: Path, reason: str) -> None:
    state = _read_source_access_state(output_dir)
    state.update(
        {
            "last_attempt": time.time(),
            "next_allowed": time.time() + SOURCE_ACCESS_BLOCK_COOLDOWN_SECONDS,
            "last_block_reason": reason,
            "successful_since_break": 0,
        }
    )
    _write_source_access_state(output_dir, state)


def _record_source_access_success(output_dir: Path) -> None:
    state = _read_source_access_state(output_dir)
    successes = int(state.get("successful_since_break") or 0) + 1
    state["last_block_reason"] = ""
    if successes >= SOURCE_ACCESS_BURST_LIMIT:
        state["successful_since_break"] = 0
        state["next_allowed"] = time.time() + SOURCE_ACCESS_BURST_COOLDOWN_SECONDS
    else:
        state["successful_since_break"] = successes
    _write_source_access_state(output_dir, state)


def choose_return_fee(minimum_selling_price: int) -> int:
    policy = build_return_shipping_fee_policy(minimum_selling_price)
    target = policy.get("target")
    if not isinstance(target, int) or target < 0:
        raise WingAutomationError("매칭 판매가로 반품배송비를 계산하지 못했습니다.")
    return target


def _return_fee_adjustment_target(
    current_fee: int,
    messages: Sequence[str],
    *,
    allow_generic_fallback: bool = False,
) -> int | None:
    """WING 안내를 읽어 반품배송비 재입력 금액을 결정한다.

    허용 상한이 표시되면 그 금액을 우선하고, WING이 금액 없이
    ``반품/교환 항목 확인``만 표시한 경우에는 사용자가 정한 10만원
    대체값을 사용한다. 최초 입력 전에는 20만원 시도를 유지한다.
    """

    normalized = [" ".join(str(message or "").split()) for message in messages]
    maximum = next(
        (
            value
            for message in normalized
            if (value := extract_return_fee_allowed_maximum(message)) is not None
        ),
        None,
    )
    if maximum is not None:
        # "○○원까지 가능"은 그 금액을 포함하는 상한이다. WING이 알려준
        # 숫자를 임의로 만원 단위로 낮추지 않고 실제 최댓값을 그대로 쓴다.
        return maximum_return_fee_at_or_below(maximum, unit=1)

    combined = " ".join(normalized)
    return_fee_rejected = "반품" in combined and any(
        token in combined for token in ("확인", "입력", "가능", "교환")
    )
    if (
        allow_generic_fallback
        and return_fee_rejected
        and current_fee > RETURN_FEE_FALLBACK_CAP
    ):
        return RETURN_FEE_FALLBACK_CAP
    return None


def _cdp_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/json/version"


def _wing_page_target(targets: Iterable[dict]) -> dict | None:
    """CDP 대상 중 현재 열려 있는 쿠팡 WING 페이지를 찾는다."""

    for target in targets:
        if target.get("type") != "page":
            continue
        parsed = urlparse(str(target.get("url") or ""))
        if parsed.netloc.lower() == "wing.coupang.com":
            return target
    return None


def _activate_or_open_wing_tab(*, port: int, url: str) -> None:
    """실행 중인 전용 Chrome에서 WING 탭을 활성화하거나 새로 연다."""

    list_url = f"http://127.0.0.1:{port}/json/list"
    with urlopen(list_url, timeout=3) as response:
        targets = json.loads(response.read().decode("utf-8"))
    target = _wing_page_target(targets if isinstance(targets, list) else [])
    if target:
        target_id = quote(str(target.get("id") or ""), safe="")
        if not target_id:
            raise WingAutomationError("열린 WING 탭의 식별값을 확인하지 못했습니다.")
        with urlopen(
            f"http://127.0.0.1:{port}/json/activate/{target_id}", timeout=3
        ):
            return

    request = Request(
        f"http://127.0.0.1:{port}/json/new?{quote(url, safe='')}",
        method="PUT",
    )
    with urlopen(request, timeout=5):
        return


def _bring_wing_window_to_front() -> bool:
    """Windows에서 WING을 표시 중인 Chrome 창을 복원하고 앞으로 가져온다."""

    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        handles: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        @callback_type
        def collect(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            class_name = ctypes.create_unicode_buffer(256)
            title = ctypes.create_unicode_buffer(1024)
            user32.GetClassNameW(hwnd, class_name, len(class_name))
            user32.GetWindowTextW(hwnd, title, len(title))
            if class_name.value.startswith("Chrome_WidgetWin") and "wing" in title.value.casefold():
                handles.append(hwnd)
            return True

        user32.EnumWindows(collect, 0)
        if not handles:
            return False
        hwnd = handles[0]
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return True
    except (AttributeError, OSError):
        return False


def _focus_controlled_chrome(
    *, profile_dir: Path, port: int, url: str
) -> None:
    try:
        _activate_or_open_wing_tab(port=port, url=url)
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        raise WingAutomationError(
            "전용 Chrome은 실행 중이지만 WING 탭을 앞으로 열지 못했습니다."
        ) from exc

    if _bring_wing_window_to_front():
        return

    # 일부 Windows 환경은 백그라운드 앱의 창 활성화를 막는다. 같은 전용
    # 프로필로 Chrome에 URL을 다시 전달하면 기존 창이 사용자 앞에 표시된다.
    chrome = find_chrome()
    if chrome is None:
        return
    subprocess.Popen(
        [str(chrome), f"--user-data-dir={profile_dir}", url],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        close_fds=True,
    )


def _wing_password_guide_return_url(url: str) -> str:
    """Return the safe WING destination embedded in its password reminder."""

    parsed = urlparse(url or "")
    if parsed.netloc.lower() != "wing.coupang.com":
        return ""
    if "/configuration/account/change-password/guide" not in parsed.path:
        return ""
    values = parse_qs(parsed.query).get("returnUrl") or []
    if not values:
        return ""
    destination = urlparse(values[0])
    if destination.netloc.lower() != "wing.coupang.com":
        return ""
    scheme = "https" if destination.scheme in {"", "http", "https"} else ""
    if not scheme:
        return ""
    return destination._replace(scheme=scheme).geturl()


def _continue_past_wing_password_guide(page, fallback_url: str) -> None:
    """Skip Coupang's optional password-change reminder when it interrupts work."""

    destination = _wing_password_guide_return_url(getattr(page, "url", ""))
    if not destination:
        return

    # Clicking the reminder's skip action records the user's choice when that
    # button exists.  WING changes this label periodically, so the safe return
    # URL remains the final fallback.
    skip = page.get_by_text(
        re.compile(r"(?:다음에|나중에).*(?:변경|하기)|건너뛰기"),
        exact=False,
    )
    visible_skip = [
        skip.nth(index)
        for index in range(skip.count())
        if skip.nth(index).is_visible()
    ]
    if visible_skip:
        try:
            visible_skip[-1].click()
            page.wait_for_timeout(500)
        except Exception:
            pass
    if _wing_password_guide_return_url(page.url):
        page.goto(
            destination or fallback_url,
            wait_until="domcontentloaded",
            timeout=60_000,
        )


def is_controlled_chrome_ready(port: int = DEFAULT_DEBUG_PORT) -> bool:
    try:
        with urlopen(_cdp_url(port), timeout=0.7) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def launch_controlled_chrome(
    *,
    profile_dir: Path,
    port: int = DEFAULT_DEBUG_PORT,
    url: str = WING_REGISTRATION_URL,
    focus_existing: bool = False,
) -> None:
    """자동입력용 Chrome을 한 번만 실행하고 로그인 상태를 계속 보존한다."""

    if is_controlled_chrome_ready(port):
        if focus_existing:
            _focus_controlled_chrome(profile_dir=profile_dir, port=port, url=url)
        return
    chrome = find_chrome()
    if chrome is None:
        raise WingAutomationError("Google Chrome을 찾지 못했습니다.")
    profile_dir.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        [
            str(chrome),
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            url,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if is_controlled_chrome_ready(port):
            if focus_existing:
                _focus_controlled_chrome(profile_dir=profile_dir, port=port, url=url)
            return
        time.sleep(0.25)
    raise WingAutomationError("자동입력용 Chrome 연결이 20초 안에 준비되지 않았습니다.")


def _connect_controlled_chrome(playwright, *, profile_dir: Path, port: int):
    """Chrome 시작 직후 CDP가 잠깐 끊겨도 자동으로 다시 연결한다."""

    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if not is_controlled_chrome_ready(port):
            try:
                launch_controlled_chrome(profile_dir=profile_dir, port=port)
            except Exception as exc:
                last_error = exc
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.5)
                continue
        try:
            return playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}"
            )
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise WingAutomationError(
        "자동입력용 Chrome 연결이 끊겼습니다. 프로그램이 Chrome을 다시 여는 데 실패했습니다."
    ) from last_error


def _notify(callback: ProgressCallback | None, stage: str, message: str) -> None:
    if callback:
        callback(stage, message)


def _type_react(locator, value: str) -> None:
    locator.click()
    locator.press("Control+A")
    locator.press_sequentially(str(value), delay=35)
    locator.press("Tab")


def _last_visible(locator):
    for index in range(locator.count() - 1, -1, -1):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    raise WingAutomationError("WING 화면에서 필요한 선택항목을 찾지 못했습니다.")


def _click_reliably(locator) -> None:
    """고정 하단바 버튼이 화면 밖으로 판정돼도 실제 클릭을 전달한다."""

    try:
        locator.scroll_into_view_if_needed(timeout=3_000)
        locator.click(timeout=5_000)
    except Exception:
        # WING의 고정 액션바는 브라우저 확대율/가로 스크롤에 따라 보이는
        # 버튼도 viewport 밖으로 판정된다. DOM click은 동일한 click 이벤트를
        # 발생시키면서 좌표 기반 판정만 우회한다.
        locator.evaluate("element => element.click()")


def _visible_button_with_text(page, text: str | re.Pattern[str]):
    """WING의 사용자 정의 버튼은 접근성 이름이 비어 있어 표시문구로 찾는다."""

    return _last_visible(page.locator("button").filter(has_text=text))


def _ancestor_with(locator, selector: str, *, minimum: int = 1, levels: int = 8):
    current = locator
    for _ in range(levels):
        current = current.locator("xpath=..")
        count = current.locator(selector).count()
        if count >= minimum:
            return current
    raise WingAutomationError("WING 입력영역 구조를 확인하지 못했습니다.")


def _preferred_wing_page(context):
    """여러 탭 중 상품등록 화면을 우선하고, 없으면 가장 최근 WING 탭을 고른다."""

    wing_pages = [
        page
        for page in context.pages
        if urlparse(getattr(page, "url", "")).netloc.lower() == "wing.coupang.com"
    ]
    registration_pages = [
        page for page in wing_pages if "vendor-inventory/formV2" in page.url
    ]
    if registration_pages:
        return registration_pages[-1]
    return wing_pages[-1] if wing_pages else None


def _wing_page(browser):
    contexts = browser.contexts
    if not contexts:
        raise WingAutomationError("자동입력 Chrome 창을 찾지 못했습니다.")
    context = contexts[0]
    return context, _preferred_wing_page(context) or context.new_page()


def _registration_search(page):
    """현재 WING 버전에서 보이는 상품 검색 입력칸을 반환한다."""

    selectors = (
        "input.new-listing-home__input",
        f'input[placeholder="{NEW_LISTING_PLACEHOLDER}"]',
        f'input[placeholder="{CATALOG_PLACEHOLDER}"]',
    )
    for selector in selectors:
        matches = page.locator(selector)
        for index in range(matches.count() - 1, -1, -1):
            candidate = matches.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue
    return None


def _wait_for_registration_search(page, timeout_ms: int = 60_000):
    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        search = _registration_search(page)
        if search is not None:
            search.wait_for(state="visible", timeout=timeout_ms)
            return search
        safety_guide = page.get_by_text(
            re.compile(r"상품등록\s*전\s*꼭\s*확인해주세요")
        )
        if any(
            safety_guide.nth(index).is_visible()
            for index in range(safety_guide.count())
        ):
            raise WingAutomationError(
                "WING의 '상품등록 전 꼭 확인해주세요' 안내가 열려 있습니다. "
                "안내 파일을 확인한 뒤 사용자가 직접 '네, 모든 내용을 확인했습니다'와 "
                "'상품등록 계속하기'를 눌러 주세요. 이후 다시 실행하면 됩니다."
            )
        page.wait_for_timeout(250)
    raise WingAutomationError(
        "WING 상품등록 시작 화면의 상품 검색창을 찾지 못했습니다."
    )


def _wait_for_registration(context, page, progress: ProgressCallback | None):
    page.on("dialog", lambda dialog: dialog.accept())
    if WING_REGISTRATION_URL not in page.url:
        page.goto(WING_REGISTRATION_URL, wait_until="domcontentloaded", timeout=60_000)
    _continue_past_wing_password_guide(page, WING_REGISTRATION_URL)
    search = _registration_search(page)
    if search is not None:
        return page
    # 로그인 화면이 별도 탭으로 열렸거나, 사용자가 이미 다른 WING 탭에서
    # 로그인을 마친 경우 현재 탭만 기다리지 않고 로그인된 WING 탭으로 전환한다.
    available = _preferred_wing_page(context)
    if available is not None and available is not page:
        page = available
        page.on("dialog", lambda dialog: dialog.accept())
    if "wing.coupang.com" in page.url and "xauth.coupang.com" not in page.url:
        # 로그인된 WING의 이전 작성 폼일 수 있다. 다음 단계에서 새 폼으로 초기화한다.
        return page
    _notify(
        progress,
        "waiting_login",
        "자동입력 Chrome에서 쿠팡 WING 로그인 후 상품등록 화면을 열어 주세요.",
    )
    deadline = time.monotonic() + LOGIN_WAIT_SECONDS
    last_navigation = 0.0
    while time.monotonic() < deadline:
        available = _preferred_wing_page(context)
        if available is not None and available is not page:
            page = available
            page.on("dialog", lambda dialog: dialog.accept())
        search = _registration_search(page)
        if search is not None:
            return page
        # 로그인 성공 뒤 WING 홈으로 이동하는 경우 등록 화면으로 다시 연결한다.
        if (
            "wing.coupang.com" in page.url
            and "vendor-inventory/formV2" not in page.url
            and time.monotonic() - last_navigation > 3
        ):
            last_navigation = time.monotonic()
            try:
                page.goto(
                    WING_REGISTRATION_URL,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
            except Exception:
                pass
        if "wing.coupang.com" in page.url and "xauth.coupang.com" not in page.url:
            return page
        page.wait_for_timeout(500)
    raise WingAutomationError(
        "10분 안에 WING 상품등록 화면을 확인하지 못했습니다. 로그인 후 다시 시도하세요."
    )


def _reset_registration_form(page) -> None:
    # 앞 상품의 검색 결과 없음/옵션 선택 모달이 남으면 배경 입력을 가로막으므로
    # 상품마다 등록 주소를 새로 열어 깨끗한 폼에서 시작한다.
    page.goto("about:blank", wait_until="domcontentloaded", timeout=30_000)
    page.goto(WING_REGISTRATION_URL, wait_until="domcontentloaded", timeout=60_000)
    _continue_past_wing_password_guide(page, WING_REGISTRATION_URL)
    _wait_for_registration_search(page)


def _open_new_registration_after_success(page) -> None:
    """등록 완료 화면을 닫고 다음 상품용 새 등록 폼을 연다."""

    links = page.get_by_text("새로운 상품등록", exact=True)
    visible_links = [
        links.nth(index)
        for index in range(links.count())
        if links.nth(index).is_visible()
    ]
    if visible_links:
        visible_links[-1].click()
    else:
        # WING 문구가 바뀌어 링크를 못 찾더라도 다음 상품이 멈추지 않게 한다.
        page.goto(WING_REGISTRATION_URL, wait_until="domcontentloaded", timeout=60_000)

    _continue_past_wing_password_guide(page, WING_REGISTRATION_URL)

    _wait_for_registration_search(page)


def _post_save_choice_visible(page) -> bool:
    """WING이 저장을 마친 뒤 띄우는 이동 선택창이 보이는지 확인한다."""

    for label in ("상품목록", "새로운 상품등록"):
        matches = page.get_by_text(label, exact=True)
        if not any(
            matches.nth(index).is_visible() for index in range(matches.count())
        ):
            return False
    return True


def _select_catalog_options_legacy(page, search_value: str) -> list[dict]:
    outer = page.locator(f'input[placeholder="{CATALOG_PLACEHOLDER}"]').first
    _type_react(outer, search_value)
    search_buttons = page.locator("button.w-btn-primary")
    _last_visible(search_buttons).click()

    modal_input = page.locator(
        f'input[placeholder="{CATALOG_MODAL_PLACEHOLDER}"]'
    ).first
    modal_input.wait_for(state="visible", timeout=20_000)
    _type_react(modal_input, search_value)
    _visible_button_with_text(page, re.compile(r"^\s*상품\s*검색\s*$")).click()

    select_buttons = page.locator("button").filter(
        has_text=re.compile(r"^\s*판매옵션\s*선택\s*$")
    )
    try:
        select_buttons.first.wait_for(state="visible", timeout=25_000)
    except Exception as exc:
        body = page.locator("body").inner_text()
        message = "카탈로그 검색 결과가 없습니다."
        if "검색 결과" in body:
            message += " WING 검색 문구를 확인하세요."
        raise WingAutomationError(message) from exc
    select_buttons.first.click()
    option_title = page.get_by_text("등록할 옵션을 선택하세요.", exact=True)
    option_title.wait_for(state="visible", timeout=20_000)

    matched: list[dict] = []
    modal = _ancestor_with(
        _last_visible(option_title), 'input[type="checkbox"]', minimum=2, levels=10
    )
    checkboxes = modal.locator('input[type="checkbox"]')
    # WING이 옵션을 모두 선택한 채 여는 경우가 있으므로 먼저 전부 해제하고
    # 아이템위너 가격이 실제로 표시된 옵션만 다시 선택한다.
    for index in range(checkboxes.count()):
        checkbox = checkboxes.nth(index)
        if checkbox.is_visible() and not checkbox.is_disabled() and checkbox.is_checked():
            checkbox.uncheck(force=True)
    selected_by_name: dict[str, tuple[int, object, int]] = {}
    for index in range(checkboxes.count()):
        checkbox = checkboxes.nth(index)
        if not checkbox.is_visible() or checkbox.is_disabled():
            continue
        current = checkbox
        card_text = ""
        for _ in range(7):
            current = current.locator("xpath=..")
            try:
                text = current.inner_text().strip()
            except Exception:
                continue
            if (
                text.count("아이템 위너 가격") == 1
                and len(text) < 1_500
            ):
                card_text = text
                break
        price = parse_winner_price(card_text)
        other_seller_count = _catalog_other_seller_count(card_text)
        # 다른 판매자가 0명인 옵션은 카탈로그 경쟁 매칭 대상이 아니다.
        # WING이 기본 체크한 상태로 열더라도 명시적으로 제외한다.
        if other_seller_count == 0:
            if checkbox.is_checked():
                checkbox.uncheck(force=True)
            continue
        if not price:
            continue
        lines = [line.strip() for line in card_text.splitlines() if line.strip()]
        name = next(
            (
                line
                for line in lines
                if "아이템 위너 가격" not in line
                and "이 상품의 다른 판매자" not in line
                and line not in {"선택", "옵션"}
            ),
            f"옵션 {len(matched) + 1}",
        )
        if _is_select_all_catalog_option(name):
            continue
        image_url = _catalog_option_image_url(checkbox)
        identity = _normalize_option_identity(name)
        existing = selected_by_name.get(identity)
        if existing is not None:
            matched_index, previous_checkbox, previous_price = existing
            if price >= previous_price:
                if checkbox.is_checked():
                    checkbox.uncheck(force=True)
                continue
            if previous_checkbox.is_checked():
                previous_checkbox.uncheck(force=True)
            matched[matched_index] = {
                "name": name,
                "winner_price": price,
                "image_url": image_url
                or str(matched[matched_index].get("image_url") or ""),
            }
            selected_by_name[identity] = (matched_index, checkbox, price)
            if not checkbox.is_checked():
                checkbox.check(force=True)
            continue
        if not checkbox.is_checked():
            checkbox.check(force=True)
        matched.append(
            {"name": name, "winner_price": price, "image_url": image_url}
        )
        selected_by_name[identity] = (len(matched) - 1, checkbox, price)

    if not matched:
        raise WingAutomationError(
            "아이템위너 가격이 표시된 옵션이 없어 판매가를 자동으로 맞출 수 없습니다."
        )
    completion = page.locator("button").filter(has_text=re.compile(r"선택완료\s*\("))
    _last_visible(completion).click()
    # 배경의 등록상품명 입력칸은 카탈로그 모달이 열린 동안에도 visible이라서
    # 해당 입력칸만 기다리면 WING 반영이 끝나기 전에 다음 단계로 넘어간다.
    # 실제 옵션 판매가 표가 생성되고 검색 모달이 닫힌 것을 함께 확인한다.
    _wait_for_option_price_editor(page, len(matched), timeout_ms=45_000)
    return matched


def _new_catalog_option_card_text(checkbox) -> str:
    current = checkbox
    best = ""
    for _ in range(9):
        current = current.locator("xpath=..")
        try:
            text = current.inner_text().strip()
        except Exception:
            continue
        if len(text) < 2_000 and len(text) > len(best):
            best = text
        if parse_winner_price(text):
            return text
    return best


def _catalog_option_image_url(checkbox) -> str:
    """카탈로그 옵션 카드에 표시된 해당 옵션 대표이미지 주소를 읽는다."""

    current = checkbox
    for _ in range(9):
        current = current.locator("xpath=..")
        images = current.locator("img")
        candidates: list[str] = []
        for index in range(min(images.count(), 12)):
            image = images.nth(index)
            for attribute in ("data-origin", "orgsrc", "src", "data-src"):
                value = _original_coupang_image_url(
                    image.get_attribute(attribute) or ""
                )
                if (
                    value.startswith("http")
                    and "coupangcdn.com" in value
                    and value not in candidates
                ):
                    candidates.append(value)
        if candidates:
            return candidates[0]
    return ""


def _select_catalog_options_new(page, search_value: str) -> list[dict]:
    """2026년 WING 새 상품등록 시작/옵션 화면에서 카탈로그를 선택한다."""

    identifier = _new_wing_identifier(search_value)
    if identifier:
        identifier_mode = page.get_by_text("상품 식별번호", exact=True)
        if identifier_mode.count():
            _last_visible(identifier_mode).click()
            identifier_input = page.locator(
                'input[placeholder="쿠팡 상품 ID, GTIN 바코드, 품번으로 검색"]'
            )
            identifier_input.first.wait_for(state="visible", timeout=10_000)
            outer = identifier_input.first
        else:
            outer = _wait_for_registration_search(page, timeout_ms=20_000)
        lookup_value = identifier
    else:
        outer = _wait_for_registration_search(page, timeout_ms=20_000)
        lookup_value = search_value
    _type_react(outer, lookup_value)
    submit = page.locator("button.new-listing-home__submit")
    if not submit.count():
        submit = page.locator("button").filter(
            has_text=re.compile(r"^\s*(?:검색|상품\s*검색)\s*$")
        )
    _last_visible(submit).click()

    search_title = page.get_by_text("등록하려는 상품을 검색해주세요", exact=True)
    try:
        search_title.first.wait_for(state="visible", timeout=30_000)
    except Exception as exc:
        raise WingAutomationError(
            "새 WING 카탈로그 검색 결과 화면이 열리지 않았습니다."
        ) from exc

    # 구매대행 상품은 옵션을 고르기 전에 해외배송 결과로 전환해야 한다.
    overseas_radio = page.locator(
        'input[name="prematching-option-delivery-method"][value="OVERSEA"]'
    )
    if overseas_radio.count():
        try:
            overseas_radio.last.check(force=True)
        except Exception:
            # 새 WING의 실제 radio는 화면 밖에 두고 부모 label만 표시하는
            # 경우가 있어 숨은 input을 직접 누르면 viewport 오류가 난다.
            overseas_radio.last.evaluate("element => element.click()")
        page.wait_for_timeout(200)
        if not overseas_radio.last.is_checked():
            raise WingAutomationError("WING 검색 결과를 해외배송으로 바꾸지 못했습니다.")
    else:
        overseas = page.get_by_text("해외 배송", exact=True)
        if overseas.count():
            _last_visible(overseas).click(force=True)
    page.wait_for_timeout(500)

    cards = page.locator(".option-card")
    try:
        cards.first.wait_for(state="visible", timeout=30_000)
    except Exception as exc:
        raise WingAutomationError(
            "카탈로그 검색 결과가 없습니다. WING 검색 문구를 확인하세요."
        ) from exc

    selected_result = None
    fallback_result = None
    for index in range(cards.count()):
        card = cards.nth(index)
        if not card.is_visible():
            continue
        fallback_result = fallback_result or card
        try:
            text = card.inner_text()
        except Exception:
            continue
        if parse_winner_price(text):
            selected_result = card
            break
    (selected_result or fallback_result).click()

    option_title = page.get_by_text("옵션 선택", exact=True)
    try:
        option_title.first.wait_for(state="visible", timeout=30_000)
    except Exception as exc:
        raise WingAutomationError("새 WING 옵션 선택 화면이 열리지 않았습니다.") from exc

    matched: list[dict] = []
    selected_by_name: dict[str, tuple[int, object, int]] = {}
    checkboxes = page.locator('.option-card input[type="checkbox"]')
    if not checkboxes.count():
        checkboxes = page.locator('input[type="checkbox"]')
    # 새 WING도 검색 결과를 기본 선택한 채 열 수 있다. 먼저 모두 해제한 뒤
    # 다른 판매자가 실제로 있고 아이템위너 가격을 읽을 수 있는 행만 고른다.
    for index in range(checkboxes.count()):
        checkbox = checkboxes.nth(index)
        if checkbox.is_visible() and not checkbox.is_disabled() and checkbox.is_checked():
            checkbox.uncheck(force=True)
    for index in range(checkboxes.count()):
        checkbox = checkboxes.nth(index)
        if not checkbox.is_visible() or checkbox.is_disabled():
            continue
        card_text = _new_catalog_option_card_text(checkbox)
        other_seller_count = _catalog_other_seller_count(card_text)
        if other_seller_count == 0:
            if checkbox.is_checked():
                checkbox.uncheck(force=True)
            continue
        price = parse_winner_price(card_text)
        if not price:
            if checkbox.is_checked():
                checkbox.uncheck(force=True)
            continue
        name = _catalog_option_name(
            card_text,
            f"옵션 {len(matched) + 1}",
        )
        image_url = _catalog_option_image_url(checkbox)
        identity = _normalize_option_identity(name)
        existing = selected_by_name.get(identity)
        if existing is not None:
            matched_index, previous_checkbox, previous_price = existing
            if price >= previous_price:
                if checkbox.is_checked():
                    checkbox.uncheck(force=True)
                continue
            if previous_checkbox.is_checked():
                previous_checkbox.uncheck(force=True)
            matched[matched_index] = {
                "name": name,
                "winner_price": price,
                "image_url": image_url
                or str(matched[matched_index].get("image_url") or ""),
            }
            selected_by_name[identity] = (matched_index, checkbox, price)
            if not checkbox.is_checked():
                checkbox.check(force=True)
            continue
        if not checkbox.is_checked():
            checkbox.check(force=True)
        matched.append(
            {"name": name, "winner_price": price, "image_url": image_url}
        )
        selected_by_name[identity] = (len(matched) - 1, checkbox, price)

    if not matched:
        raise WingAutomationError(
            "자동등록 가능한 아이템위너 옵션이 없습니다. 다른 판매자 0명, "
            "품절 또는 이미 등록된 옵션만 있는 상품이므로 자동 재시도 대상이 "
            "아닙니다. WING에서 판매 가능한 옵션이 생긴 뒤 다시 시도하세요."
        )

    completion = page.locator("button").filter(
        has_text=re.compile(r"^\s*[1-9][0-9,]*개\s*상품\s*등록\s*$")
    )
    if not completion.count():
        raise WingAutomationError("선택한 옵션을 등록 폼에 넣는 버튼을 찾지 못했습니다.")
    _last_visible(completion).click()
    _wait_for_option_price_editor(page, len(matched), timeout_ms=60_000)
    return matched


def _select_catalog_options(page, search_value: str) -> list[dict]:
    if page.locator("input.new-listing-home__input").count():
        return _select_catalog_options_new(page, search_value)
    return _select_catalog_options_legacy(page, search_value)


def _infer_required_option_values(
    attribute: dict,
    attributes: list[dict],
    matched: list[dict],
    product_id: str,
) -> list[str]:
    """카탈로그에서 비어 온 필수 옵션에 보수적인 대체값을 정한다."""

    type_name = str(attribute.get("type_name") or "")
    component = str(attribute.get("component") or "")
    optional_values: list[str] = []
    for candidate in attributes:
        if candidate.get("mandatory"):
            continue
        for value in candidate.get("values") or []:
            cleaned = str(value).strip()
            if cleaned and cleaned not in optional_values:
                optional_values.append(cleaned)

    numeric_tokens = (
        "수량",
        "중량",
        "용량",
        "길이",
        "높이",
        "너비",
        "폭",
        "두께",
        "지름",
    )
    if (
        any(token in type_name for token in numeric_tokens)
        or component.endswith("/Numeric")
        or "unit" in component.lower()
    ):
        return ["1"]

    # 의류 카테고리는 카탈로그 옵션명에 사이즈가 포함되어 있어도 WING이
    # 필수 '패션의류/잡화 사이즈'로 연결하지 못하는 경우가 있다. 이때는
    # 3XL/L/XL/M 같은 실제 값들을 추출하고, 정말 값이 없을 때만 기본형을 쓴다.
    if "사이즈" in type_name or "크기" in type_name:
        names = [str(option.get("name") or "").strip() for option in matched]
        sizes = _standard_size_values(optional_values + names)
        if sizes:
            return sizes
        return ["기본형"]

    names = [str(option.get("name") or "").strip() for option in matched]
    names = [name for name in names if name]
    if "색상" in type_name:
        colors = (
            "블랙",
            "화이트",
            "실버",
            "골드",
            "레드",
            "블루",
            "그린",
            "핑크",
            "베이지",
            "브라운",
            "그레이",
            "검정",
            "흰색",
            "노랑",
            "옐로",
            "보라",
            "퍼플",
            "오렌지",
            "주황",
            "투명",
        )
        found = next(
            (
                color
                for value in optional_values + names
                for color in colors
                if color in value
            ),
            "",
        )
        return [found or "혼합색상"]

    if "모델" in type_name or "품번" in type_name:
        if optional_values:
            return optional_values
        descriptive = [
            name
            for name in names
            if not re.fullmatch(r"\d+\s*(?:개|세트|박스)", name)
        ]
        return descriptive or [product_id or "기본형"]

    return optional_values[:1] or names[:1] or [product_id or "기본형"]


def _complete_missing_required_options(
    page,
    matched: list[dict],
    *,
    product_id: str,
    _allow_intermediate_save: bool = True,
) -> list[str]:
    """카탈로그 매칭 뒤 비어 있는 WING 필수 옵션을 실제 옵션값으로 완성한다."""

    # 2026년 하반기 WING은 카탈로그 옵션별 필수 속성을 별도 팝업에서
    # 연결하도록 바뀌었다. 예전처럼 옵션값 목록에 필수 사이즈를 추가하면
    # 색상×사이즈의 불필요한 조합이 만들어지므로, 새 팝업이 보이면 각
    # 카탈로그 행의 원본 옵션값을 필수 속성에 1:1로 연결한다.
    completion_buttons = page.get_by_role(
        "button", name="옵션 완성하기", exact=True
    )
    completion_button = next(
        (
            completion_buttons.nth(index)
            for index in range(completion_buttons.count())
            if completion_buttons.nth(index).is_visible()
        ),
        None,
    )
    if completion_button is not None:
        completion_button.click()
        page.wait_for_timeout(500)

        option_name_table = page.locator("table.table-node").filter(
            has=page.get_by_role("button", name="수정", exact=True)
        )
        if not option_name_table.count():
            raise WingAutomationError(
                "WING의 새 필수 옵션 연결표를 찾지 못했습니다."
            )

        option_rows = option_name_table.last.locator("tr")
        row_records: list[dict] = []
        unmatched_names: list[str] = []
        used_records: set[tuple[str, int]] = set()
        for index in range(option_rows.count()):
            row = option_rows.nth(index)
            option_name = _required_option_row_name(row.inner_text())
            record = _matched_option_record(option_name, matched)
            # 옵션 수가 같을 때는 WING이 표시명을 축약한 경우에만 기존 순서를
            # 보조 기준으로 쓴다. 수가 다르면 이름이 유일하게 맞는 행만 허용한다.
            if record is None and option_rows.count() == len(matched):
                record = matched[index]
            if record is None:
                unmatched_names.append(option_name or f"{index + 1}번째 옵션")
                continue
            key = (
                _normalize_option_identity(str(record.get("name") or "")),
                int(record.get("winner_price") or 0),
            )
            if key in used_records:
                unmatched_names.append(option_name or f"{index + 1}번째 옵션")
                continue
            used_records.add(key)
            row_records.append(record)

        if unmatched_names or len(row_records) != option_rows.count():
            preview = ", ".join(unmatched_names[:5])
            raise WingAutomationError(
                "WING 필수 옵션을 카탈로그 옵션명과 1:1로 연결하지 못했습니다"
                + (f": {preview}" if preview else ".")
            )

        # WING이 중복/판매불가 옵션을 자동으로 제외해 행 수가 줄어든 경우,
        # 이후 가격·재고 입력도 실제 화면에 남은 옵션만 사용한다.
        if len(row_records) != len(matched):
            matched[:] = row_records

        for index in range(option_rows.count()):
            row = option_rows.nth(index)
            parts = [
                part.strip()
                for part in row.inner_text().replace("수정", "").split(",")
                if part.strip()
            ]
            # 이미 원본 옵션값과 같은 필수 속성이 연결된 행은 건너뛴다.
            if len(parts) >= 3 and parts[-1] == parts[0]:
                continue

            row.get_by_role("button", name="수정", exact=True).click()
            inner_popup = page.locator(".modify-inner-popup").last
            inner_popup.wait_for(state="visible", timeout=5_000)
            textboxes = inner_popup.get_by_role("textbox")
            if textboxes.count() < 2:
                raise WingAutomationError(
                    "WING 필수 옵션 수정창의 입력값을 찾지 못했습니다."
                )
            source_value = textboxes.nth(textboxes.count() - 1).input_value().strip()
            if not source_value:
                source_value = parts[0] if parts else str(
                    row_records[index].get("name") or product_id or "기본형"
                )

            # WING의 새 옵션 입력기는 fill()로 값이 보여도 Vue 모델이 갱신되지
            # 않는다. 실제 키 입력 이벤트를 발생시켜야 확인/저장이 동작한다.
            editable = textboxes.nth(0)
            editable.click()
            editable.press("Control+A")
            editable.press("Backspace")
            editable.press_sequentially(source_value, delay=35)
            inner_popup.get_by_role(
                "button", name="확인", exact=True
            ).click(force=True)
            inner_popup.wait_for(state="detached", timeout=5_000)

        product_tables = page.locator("table.table-node").filter(
            has_text=product_id
        )
        if not product_tables.count():
            raise WingAutomationError(
                "WING 필수 옵션 창의 판매가 입력표를 찾지 못했습니다."
            )
        price_rows = product_tables.last.locator("tr")
        deadline = time.monotonic() + 5
        while price_rows.count() != option_rows.count() and time.monotonic() < deadline:
            page.wait_for_timeout(250)
            price_rows = product_tables.last.locator("tr")
        if price_rows.count() != option_rows.count():
            raise WingAutomationError(
                "WING 필수 옵션명 행과 가격 행의 수가 다릅니다."
            )
        for index in range(price_rows.count()):
            fields = price_rows.nth(index).get_by_role("textbox")
            if fields.count() < 2:
                raise WingAutomationError(
                    "WING 필수 옵션 창의 정상가/판매가 입력칸을 찾지 못했습니다."
                )
            price = int(row_records[index].get("winner_price") or 0)
            if price <= 0:
                raise WingAutomationError(
                    "카탈로그 옵션의 아이템위너 가격을 확인하지 못했습니다."
                )
            _type_react(fields.nth(0), str(price))
            _type_react(fields.nth(1), str(price))

        save_button = _last_visible(
            page.get_by_role("button", name="저장", exact=True)
        )
        save_button.click(force=True)
        page.wait_for_timeout(1_000)

        invalid_id = page.get_by_text("request inventoryId invalid", exact=True)
        if any(
            invalid_id.nth(index).is_visible()
            for index in range(invalid_id.count())
        ):
            confirms = page.get_by_role("button", name="확인", exact=True)
            for index in range(confirms.count() - 1, -1, -1):
                if confirms.nth(index).is_visible():
                    confirms.nth(index).click(force=True)
                    break
            if not _allow_intermediate_save:
                raise WingAutomationError(
                    "중간저장 후에도 쿠팡 임시 상품 ID가 생성되지 않았습니다"
                    "(request inventoryId invalid)."
                )

            # 새 상품은 옵션 매칭 API가 inventoryId를 요구한다. 아직 배송·이미지·
            # 고시 등 필수값을 넣기 전인 이 시점에 중간저장을 누르면 WING이 ID를
            # 만들지 않는다. 수정창을 닫고 나머지 필수값을 모두 채운 뒤 재시도한다.
            modal_titles = page.get_by_text("옵션 수정", exact=True)
            modal_title = _visible_or_none(modal_titles)
            if modal_title is not None:
                dismiss = modal_title.locator(
                    'button[data-wuic-action="dismiss"]'
                )
                if dismiss.count():
                    dismiss.last.click(force=True)
                    page.wait_for_timeout(400)
                    _dismiss_visible_wing_confirmation(page, wait_ms=2_000)
            raise _DeferredOptionMatching(
                "필수정보 입력 후 쿠팡 임시 상품 ID를 생성해야 합니다."
            )

        page.wait_for_timeout(500)
        missing_banner = page.get_by_text(
            re.compile(r"누락된 필수 옵션 정보를 입력해주세요")
        )
        if any(
            missing_banner.nth(index).is_visible()
            for index in range(missing_banner.count())
        ):
            raise WingAutomationError(
                "필수 옵션값은 입력했지만 WING이 옵션 매칭 저장을 완료하지 "
                "않았습니다. 중간저장 후 다시 실행해주세요."
            )
        return [f"필수 옵션 {len(row_records)}개를 카탈로그 행별로 자동 연결"]

    def read_attributes() -> list[dict]:
        return page.evaluate(
            """() => Array.from(document.querySelectorAll('.attribute')).map((el, index) => {
            const renderer = el.__vue__ || (el.parentElement && el.parentElement.__vue__);
            const config = renderer && renderer.config;
            const props = config && config.props;
            if (!config || !props) return null;
            el.setAttribute('data-codex-config-key', config.configKey);
            let rawTypeName = props.typeName || (
                props.valueList && props.valueList[0] && props.valueList[0].data &&
                props.valueList[0].data[0] && props.valueList[0].data[0].typeName
            ) || '';
            if (typeof rawTypeName === 'string' && /^[\\[{]/.test(rawTypeName.trim())) {
                try { rawTypeName = JSON.parse(rawTypeName); } catch (_) {}
            }
            const typeName = Array.isArray(rawTypeName)
                ? rawTypeName.map(item => (
                    item && (item.typeName || item.name || item.valueName) || item || ''
                )).filter(Boolean).join('/')
                : rawTypeName;
            return {
                index,
                config_key: config.configKey,
                component: config.name,
                type_name: typeName,
                type_ids: props.typeIds || [],
                mandatory: Boolean(props.mandatory),
                values: (props.valueList || []).flatMap(item =>
                    (item.data || []).map(value => value.valueName).filter(Boolean)
                ),
            };
        }).filter(Boolean)"""
        )

    # WING은 카탈로그 선택 직후 필수 옵션을 순차적으로 렌더링한다. 첫 번째
    # 옵션만 보인 순간 검사하면 뒤늦게 나타나는 필수 사이즈 등을 놓칠 수 있다.
    page.wait_for_timeout(1_200)
    attributes: list[dict] = []
    previous_signature: tuple | None = None
    stable_reads = 0
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        attributes = read_attributes()
        signature = tuple(
            (
                item.get("config_key"),
                item.get("type_name"),
                item.get("mandatory"),
                tuple(item.get("values") or ()),
            )
            for item in attributes
        )
        if signature and signature == previous_signature:
            stable_reads += 1
        else:
            stable_reads = 0
            previous_signature = signature
        if stable_reads >= 2:
            break
        page.wait_for_timeout(400)
    missing = [
        attribute
        for attribute in attributes
        if attribute.get("mandatory") and not attribute.get("values")
    ]
    if not missing:
        return []

    notes: list[str] = []
    for attribute in missing:
        config_key = str(attribute["config_key"])
        enabled = page.evaluate(
            """(configKey) => {
                const element = Array.from(document.querySelectorAll('.attribute')).find(el => {
                    const renderer = el.__vue__ || (el.parentElement && el.parentElement.__vue__);
                    return renderer && renderer.config && renderer.config.configKey === configKey;
                });
                if (!element) return false;
                const renderer = element.__vue__ || element.parentElement.__vue__;
                element.setAttribute('data-codex-config-key', configKey);
                renderer.thisProps.preMatching = false;
                renderer.thisProps.editStatus = 'EDIT';
                renderer.config.props.preMatching = false;
                renderer.config.props.editStatus = 'EDIT';
                const component = renderer.$children[0];
                if (!component) return false;
                component._props.editStatus = 'EDIT';
                component.$forceUpdate();
                renderer.$forceUpdate();
                return true;
            }""",
            config_key,
        )
        if not enabled:
            raise WingAutomationError(
                f"필수 구매 옵션 '{attribute.get('type_name')}'을 활성화하지 못했습니다."
            )

        def current_container(*, require_input: bool = False):
            """Vue 갱신 뒤 교체된 필수옵션 DOM을 configKey로 다시 찾는다."""

            selector = f'.attribute[data-codex-config-key="{config_key}"]'
            input_selector = (
                'input[placeholder="옵션값 입력"], '
                'input[placeholder="숫자만 입력"], '
                'input[placeholder*="옵션값을 정확히 입력"]'
            )
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                page.evaluate(
                    """(configKey) => {
                        const element = Array.from(document.querySelectorAll('.attribute')).find(el => {
                            const renderer = el.__vue__ || (el.parentElement && el.parentElement.__vue__);
                            return renderer && renderer.config && renderer.config.configKey === configKey;
                        });
                        if (!element) return false;
                        element.setAttribute('data-codex-config-key', configKey);
                        return true;
                    }""",
                    config_key,
                )
                candidate = page.locator(selector)
                if candidate.count() and (
                    not require_input or candidate.last.locator(input_selector).count()
                ):
                    return candidate.last
                page.wait_for_timeout(200)
            raise WingAutomationError(
                f"필수 구매 옵션 '{attribute.get('type_name')}' 입력칸이 "
                "WING 화면 갱신 후 나타나지 않았습니다."
            )

        page.wait_for_timeout(400)
        values = _infer_required_option_values(
            attribute, attributes, matched, product_id
        )
        for value in values:
            container = current_container(require_input=True)
            input_field = container.locator(
                'input[placeholder="옵션값 입력"], '
                'input[placeholder="숫자만 입력"], '
                'input[placeholder*="옵션값을 정확히 입력"]'
            ).last
            # 단위형 입력은 input 이벤트만으로는 WING 검증값이 갱신되지 않고
            # blur까지 발생해야 '추가'가 활성화된다.
            _type_react(input_field, value)
            page.wait_for_timeout(250)
            container = current_container()
            add = container.get_by_text("추가", exact=True)
            if not add.count():
                raise WingAutomationError(
                    f"필수 구매 옵션 '{attribute.get('type_name')}' 값을 추가하지 못했습니다."
                )
            add.click()
            page.wait_for_timeout(600)
        notes.append(
            f"필수 옵션 {attribute.get('type_name')}={', '.join(values)} 자동 보완"
        )

    # WING은 카테고리 옵션을 최대 3개까지만 허용한다. 필수 옵션으로 옮긴 값과
    # 중복되는 선택형 사용자 옵션부터 제거해 옵션 수를 제한한다.
    removable = [
        attribute
        for attribute in attributes
        if not attribute.get("mandatory") and attribute.get("values")
    ]
    remaining_count = len(attributes)
    for attribute in reversed(removable):
        if remaining_count <= 3:
            break
        removed = page.evaluate(
            """(configKey) => {
                const element = Array.from(document.querySelectorAll('.attribute')).find(el => {
                    const renderer = el.__vue__ || (el.parentElement && el.parentElement.__vue__);
                    return renderer && renderer.config && renderer.config.configKey === configKey;
                });
                if (!element) return false;
                const renderer = element.__vue__ || element.parentElement.__vue__;
                const component = renderer.$children[0];
                const payload = (component && component.valueList || []).flatMap(
                    item => item.data || []
                );
                if (!component || !payload.length) return false;
                component.$emit('removeAttribute', payload);
                return true;
            }""",
            str(attribute["config_key"]),
        )
        if removed:
            remaining_count -= 1
            notes.append(
                f"중복 옵션 {attribute.get('type_name') or '사용자 옵션'} 자동 제거"
            )
            page.wait_for_timeout(500)
    if remaining_count > 3:
        raise WingAutomationError(
            "WING의 최대 3개 옵션 제한에 맞게 중복 옵션을 정리하지 못했습니다."
        )
    return notes


def _generic_option_grid_state(page) -> dict[str, int]:
    """WING 클래스명이 바뀌어도 옵션 행의 판매가·재고 입력쌍을 찾는다."""

    try:
        result = page.evaluate(
            """() => {
                const visible = element => Boolean(
                    element && !element.disabled
                    && (element.offsetWidth || element.offsetHeight
                        || element.getClientRects().length)
                );
                const roots = Array.from(document.querySelectorAll(
                    '#optionPaneTable, [class*="option-pane-table"], '
                    + '[data-component*="option"], [class*="option-price"]'
                )).filter(visible);
                const root = roots.find(element => {
                    const text = (element.innerText || '').replace(/\\s+/g, ' ');
                    return /판매가|아이템\\s*위너|재고/.test(text)
                        || element.querySelector('[data-item-key="salePrice"], '
                            + '[name*="salePrice" i], [id*="salePrice" i]');
                });
                if (!root) return {rows: 0, inputs: 0};
                const inputs = Array.from(root.querySelectorAll(
                    'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])'
                )).filter(visible);
                const groups = [];
                const seen = new Set();
                for (const input of inputs) {
                    const row = input.closest(
                        'tr, [role="row"], .option-pane-table-row, '
                        + '[data-item-index], [class*="option-row"], [class*="table-row"]'
                    );
                    if (!row || seen.has(row)) continue;
                    seen.add(row);
                    const fields = Array.from(row.querySelectorAll(
                        'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])'
                    )).filter(visible);
                    const semantics = fields.map(field => [
                        field.getAttribute('name'), field.id,
                        field.getAttribute('data-item-key'),
                        field.getAttribute('placeholder'),
                        field.getAttribute('aria-label'),
                        field.closest('[data-item-key]')?.getAttribute('data-item-key')
                    ].filter(Boolean).join(' ').toLowerCase());
                    const price = semantics.some(value =>
                        /sale.?price|판매가/.test(value)
                    );
                    const stock = semantics.some(value =>
                        /maximum.?buy.?count|stock|inventory|재고/.test(value)
                    );
                    if ((price && stock) || fields.length >= 3) groups.push(row);
                }
                return {rows: groups.length, inputs: inputs.length};
            }"""
        )
    except Exception:
        return {"rows": 0, "inputs": 0}
    return {
        "rows": int((result or {}).get("rows") or 0),
        "inputs": int((result or {}).get("inputs") or 0),
    }


def _wake_option_price_editor(page) -> None:
    """화면 밖에 있어 비어 있는 WING 가상 옵션표의 입력 열을 렌더링한다."""

    try:
        page.evaluate(
            """() => {
                const table = document.querySelector(
                    '#optionPaneTable, .scrollable-option-pane-table, '
                    + '[class*="option-pane-table"]'
                );
                if (!table) return false;
                table.scrollIntoView({block: 'center', inline: 'nearest'});
                const scrollers = [
                    table,
                    table.querySelector('#optionPaneTableBody'),
                    table.querySelector('.virtual-scroll-container')
                ].filter(Boolean);
                for (const element of scrollers) {
                    element.scrollTop = Math.min(2, element.scrollHeight);
                    element.scrollLeft = Math.max(0, element.scrollWidth - element.clientWidth);
                    element.dispatchEvent(new Event('scroll', {bubbles: true}));
                }
                window.dispatchEvent(new Event('resize'));
                return true;
            }"""
        )
        page.wait_for_timeout(700)
    except Exception:
        pass


def _option_price_editor_state(page, matched_count: int) -> dict[str, int | bool]:
    """현재 WING 옵션 판매가 편집기의 렌더링 상태를 읽는다."""

    modal_inputs = page.locator(
        f'input[placeholder="{CATALOG_MODAL_PLACEHOLDER}"]'
    )
    modal_visible = any(
        modal_inputs.nth(index).is_visible() for index in range(modal_inputs.count())
    )

    table_rows = 0
    tables = page.locator("table")
    for index in range(tables.count()):
        table = tables.nth(index)
        if not table.is_visible():
            continue
        try:
            normalized = table.inner_text().replace(" ", "")
        except Exception:
            continue
        if "아이템위너가격" not in normalized:
            continue
        rows = 0
        for row_index in range(table.locator("tr").count()):
            row = table.locator("tr").nth(row_index)
            if row.is_visible() and row.locator('input[type="text"]').count() >= 3:
                rows += 1
        table_rows = max(table_rows, rows)

    candidates = page.locator('input[type="text"][placeholder=""]')
    blank_inputs = sum(
        1
        for index in range(candidates.count())
        if candidates.nth(index).is_visible() and not candidates.nth(index).is_disabled()
    )
    bulk_price = page.get_by_text("판매가 일괄입력", exact=True)
    bulk_stock = page.get_by_text("재고수량 일괄입력", exact=True)
    bulk_controls_visible = any(
        bulk_price.nth(index).is_visible() for index in range(bulk_price.count())
    ) and any(
        bulk_stock.nth(index).is_visible() for index in range(bulk_stock.count())
    )
    virtual_rows = page.locator(".option-pane-table-row")
    visible_virtual_rows = sum(
        1
        for index in range(virtual_rows.count())
        if virtual_rows.nth(index).is_visible()
        and virtual_rows.nth(index).locator('[data-item-key="salePrice"] input').count()
        and virtual_rows.nth(index).locator(
            '[data-item-key="maximumBuyCount"] input'
        ).count()
    )
    option_total = _parse_option_total(page.locator("body").inner_text())
    easy_price_rows = page.locator(
        ".easy-price-table tbody tr.row-group-start"
    )
    easy_stock_rows = page.locator(
        '.easy-add-table tbody tr:has(input[type="number"][placeholder="0"])'
    )
    visible_easy_price_rows = sum(
        1
        for index in range(easy_price_rows.count())
        if easy_price_rows.nth(index).is_visible()
        and easy_price_rows.nth(index).locator(".sale-price-cell input").count()
    )
    visible_easy_stock_rows = sum(
        1
        for index in range(easy_stock_rows.count())
        if easy_stock_rows.nth(index).is_visible()
    )
    completion_buttons = page.get_by_text("옵션 완성하기", exact=True)
    required_options_pending = any(
        completion_buttons.nth(index).is_visible()
        for index in range(completion_buttons.count())
    )
    generic_grid = _generic_option_grid_state(page)
    ready = (
        not modal_visible
        and (
            table_rows >= matched_count
            or (
                visible_easy_price_rows >= matched_count
                and visible_easy_stock_rows >= matched_count
            )
            or (option_total >= matched_count and visible_virtual_rows > 0)
            # 카탈로그 옵션은 반영됐지만 필수 속성이 비어 있으면 가격표보다
            # '옵션 완성하기'가 먼저 나타난다. 이 상태를 준비 완료로 넘겨
            # 다음 단계가 필수 속성을 채우도록 한다.
            or (option_total >= matched_count and required_options_pending)
            or generic_grid["rows"] >= matched_count
            or (blank_inputs >= matched_count * 6 and bulk_controls_visible)
        )
    )
    return {
        "ready": ready,
        "modal_visible": modal_visible,
        "table_rows": table_rows,
        "blank_inputs": blank_inputs,
        "bulk_controls_visible": bulk_controls_visible,
        "virtual_rows": visible_virtual_rows,
        "option_total": option_total,
        "easy_price_rows": visible_easy_price_rows,
        "easy_stock_rows": visible_easy_stock_rows,
        "required_options_pending": required_options_pending,
        "generic_rows": generic_grid["rows"],
        "generic_inputs": generic_grid["inputs"],
    }


def _wait_for_option_price_editor(
    page, matched_count: int, *, timeout_ms: int = 30_000
) -> dict[str, int | bool]:
    """카탈로그 옵션이 실제 판매가 입력표로 반영될 때까지 기다린다."""

    deadline = time.monotonic() + timeout_ms / 1_000
    last_state: dict[str, int | bool] = {}
    stable_reads = 0
    reads = 0
    while time.monotonic() < deadline:
        last_state = _option_price_editor_state(page, matched_count)
        if last_state["ready"]:
            stable_reads += 1
            if stable_reads >= 4:
                return last_state
        else:
            stable_reads = 0
            # 옵션명은 생성됐지만 판매가·재고 입력칸이 0개인 신형 화면은
            # 옵션 영역이 viewport에 들어오기 전까지 가상 행을 만들지 않는다.
            reads += 1
            if last_state.get("option_total", 0) >= matched_count and reads % 4 == 0:
                _wake_option_price_editor(page)
        page.wait_for_timeout(250)
    option_summary = page.evaluate(
        """() => {
            const visible = el => Boolean(
                el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            );
            const attributes = Array.from(document.querySelectorAll('.attribute'))
                .filter(visible)
                .map(el => (el.innerText || '').replace(/\\s+/g, ' ').trim())
                .filter(Boolean);
            const body = document.body && document.body.innerText || '';
            const start = body.indexOf('옵션');
            const end = body.indexOf('상품이미지', Math.max(0, start));
            const optionText = start >= 0
                ? body.slice(start, end > start ? end : start + 1200)
                : '';
            return {
                attributes,
                optionText: optionText.replace(/\\s+/g, ' ').trim().slice(0, 1200),
            };
        }"""
    )
    raise WingAutomationError(
        "카탈로그 옵션 선택 결과가 WING 판매가 입력표에 반영되지 않았습니다. "
        f"(검색창 표시={last_state.get('modal_visible', False)}, "
        f"옵션행={last_state.get('table_rows', 0)}, "
        f"가상행={last_state.get('virtual_rows', 0)}/"
        f"{last_state.get('option_total', 0)}, "
        f"신형행={last_state.get('generic_rows', 0)}, "
        f"입력칸={last_state.get('blank_inputs', 0)}, "
        f"옵션속성={option_summary.get('attributes', [])}, "
        f"화면={option_summary.get('optionText', '')})"
    )


def _fill_virtual_option_prices_and_stock(page, matched: list[dict]) -> bool:
    """WING 가상 스크롤 옵션표의 모든 행에 판매가와 재고를 입력한다."""

    expected_count = len(matched)
    container = page.locator(".option-pane-table-body.virtual-scroll-container")
    if not container.count() or not _last_visible(container).is_visible():
        return False
    scroll = _last_visible(container)
    filled: set[str] = set()
    stagnant_reads = 0

    def set_without_scrolling(field, value: int) -> bool:
        expected = str(value)
        for _ in range(3):
            field.evaluate(
                """(element, newValue) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(element, String(newValue));
                    element.dispatchEvent(new Event('input', {bubbles: true}));
                    element.dispatchEvent(new Event('change', {bubbles: true}));
                    element.dispatchEvent(new Event('blur', {bubbles: true}));
                }""",
                value,
            )
            page.wait_for_timeout(120)
            try:
                actual = re.sub(r"\D", "", field.input_value())
            except Exception:
                actual = ""
            if actual == expected:
                return True
        return False

    for _ in range(30):
        count_before = len(filled)
        rows = page.locator(".option-pane-table-row")
        for index in range(rows.count()):
            row = rows.nth(index)
            sale = row.locator('[data-item-key="salePrice"] input')
            stock = row.locator('[data-item-key="maximumBuyCount"] input')
            if not row.is_visible() or not sale.count() or not stock.count():
                continue
            name_cells = row.locator(".option-pane-table-cell")
            name = (
                name_cells.nth(1).inner_text().strip()
                if name_cells.count() > 1
                else row.inner_text().splitlines()[0].strip()
            )
            key = re.sub(r"\s+", " ", name)
            if not key or key in filled:
                continue
            winner_cell = row.locator(".pre-matching").first
            winner_price = _parse_option_row_winner_price(
                winner_cell.inner_text() if winner_cell.count() else ""
            )
            if not winner_price:
                winner_price = _matched_option_winner_price(key, matched)
            if not winner_price:
                raise WingAutomationError(
                    f"옵션 '{key}'의 아이템위너 가격을 읽지 못했습니다."
                )
            # Locator.type()이 가상 목록의 경계 행을 자동 스크롤하면 그 순간
            # DOM 행이 교체되어 다른 옵션에 값이 들어갈 수 있다. 스크롤 없이
            # input/change/blur 이벤트를 보내고 실제 값까지 확인한다.
            if not set_without_scrolling(sale.first, winner_price):
                raise WingAutomationError(
                    f"옵션 '{key}' 판매가 {winner_price:,}원이 반영되지 않았습니다."
                )
            if not set_without_scrolling(stock.first, DEFAULT_STOCK_QUANTITY):
                raise WingAutomationError(
                    f"옵션 '{key}' 재고 999개가 반영되지 않았습니다."
                )
            filled.add(key)

        state = scroll.evaluate(
            "el => ({top: el.scrollTop, height: el.clientHeight, total: el.scrollHeight})"
        )
        top = int(state.get("top") or 0)
        height = int(state.get("height") or 0)
        total = int(state.get("total") or 0)
        if len(filled) >= expected_count:
            scroll.evaluate("el => { el.scrollTop = 0; }")
            return True
        if len(filled) == count_before:
            stagnant_reads += 1
        else:
            stagnant_reads = 0
        if stagnant_reads >= 4:
            break
        # 마지막으로 보이는 입력칸을 채우는 과정에서 Playwright가 스크롤을
        # 먼저 끝까지 내릴 수 있다. 이때 바로 종료하지 않고 같은 위치에서
        # 가상 목록이 다음 행들로 교체될 시간을 준다.
        next_top = (
            max(0, total - height)
            if top + height >= total - 2
            else min(total, top + max(240, height - 96))
        )
        scroll.evaluate("(el, value) => { el.scrollTop = value; }", next_top)
        page.wait_for_timeout(550)

    scroll.evaluate("el => { el.scrollTop = 0; }")
    raise WingAutomationError(
        f"가상 옵션표 {expected_count}개 중 {len(filled)}개만 판매가·재고를 입력했습니다."
    )


def _fill_easy_option_prices_and_stock(page, matched: list[dict]) -> bool:
    """2026년 새 WING의 easy-add/easy-price 표를 채운다."""

    def set_stable_value(field, value: int) -> tuple[bool, int]:
        """React가 매 키 입력마다 행을 다시 그려도 값을 안정적으로 넣는다."""

        expected = int(value)
        last_actual = -1
        for attempt in range(4):
            try:
                # 키를 한 글자씩 입력하면 새 WING의 금액 포맷터가 첫 숫자에서
                # 행을 다시 그려 뒤 숫자를 잃을 수 있다. 네이티브 value setter로
                # 한 번에 넣고 React가 듣는 이벤트를 순서대로 보낸다.
                field.evaluate(
                    """(element, newValue) => {
                        const setter = Object.getOwnPropertyDescriptor(
                            HTMLInputElement.prototype, 'value'
                        ).set;
                        setter.call(element, String(newValue));
                        element.dispatchEvent(new Event('input', {bubbles: true}));
                        element.dispatchEvent(new Event('change', {bubbles: true}));
                        element.dispatchEvent(new Event('blur', {bubbles: true}));
                    }""",
                    expected,
                )
            except Exception:
                if attempt == 3:
                    break
            page.wait_for_timeout(300)

            stable_reads = 0
            for _ in range(3):
                try:
                    last_actual = _parse_money_input(field.input_value())
                except Exception:
                    last_actual = -1
                if last_actual != expected:
                    break
                stable_reads += 1
                page.wait_for_timeout(180)
            if stable_reads == 3:
                return True, last_actual

            # 네이티브 이벤트를 무시하는 빌드에서는 fill을 한 번에 사용한다.
            try:
                field.fill(str(expected), force=True)
                field.dispatch_event("change")
                field.dispatch_event("blur")
            except Exception:
                continue
            page.wait_for_timeout(350)
            try:
                last_actual = _parse_money_input(field.input_value())
            except Exception:
                last_actual = -1
            if last_actual == expected:
                page.wait_for_timeout(250)
                if _parse_money_input(field.input_value()) == expected:
                    return True, expected
        return False, last_actual

    price_rows = page.locator(".easy-price-table tbody tr.row-group-start")
    stock_rows = page.locator(
        '.easy-add-table tbody tr:has(input[type="number"][placeholder="0"])'
    )
    if not price_rows.count() and not stock_rows.count():
        return False
    if price_rows.count() < len(matched) or stock_rows.count() < len(matched):
        raise WingAutomationError(
            "새 WING 옵션표의 판매가 행과 재고 행 수가 선택 옵션 수와 다릅니다."
        )

    remaining = list(matched)
    ordered: list[dict] = []
    for index in range(price_rows.count()):
        row = price_rows.nth(index)
        if not row.is_visible():
            continue
        winner_price = _parse_option_row_winner_price(row.inner_text())
        option = next(
            (
                candidate
                for candidate in remaining
                if candidate.get("winner_price") == winner_price
            ),
            None,
        )
        if option is None and remaining:
            option = remaining[0]
        if option is None:
            break
        remaining.remove(option)
        sale = row.locator(".sale-price-cell input").first
        applied, actual = set_stable_value(sale, option["winner_price"])
        if not applied:
            actual_text = "빈 값" if actual < 0 else f"{actual:,}원"
            raise WingAutomationError(
                f"옵션 '{option['name']}' 판매가 {option['winner_price']:,}원이 "
                f"새 WING 가격표에 반영되지 않았습니다. 실제 표시값: {actual_text}"
            )
        ordered.append(option)

    visible_stock_rows = [
        stock_rows.nth(index)
        for index in range(stock_rows.count())
        if stock_rows.nth(index).is_visible()
    ]
    if len(ordered) != len(matched) or len(visible_stock_rows) < len(ordered):
        raise WingAutomationError("새 WING 옵션표의 옵션 순서를 확인하지 못했습니다.")
    for index, option in enumerate(ordered):
        row = visible_stock_rows[index]
        stock = row.locator('input[type="number"][placeholder="0"]').first
        applied, actual = set_stable_value(stock, DEFAULT_STOCK_QUANTITY)
        if not applied:
            actual_text = "빈 값" if actual < 0 else f"{actual:,}개"
            raise WingAutomationError(
                f"옵션 '{option['name']}' 재고 999개가 새 WING 옵션표에 "
                f"반영되지 않았습니다. 실제 표시값: {actual_text}"
            )
    return True


def _fill_generic_option_prices_and_stock(page, matched: list[dict]) -> bool:
    """속성/클래스명이 달라진 신형 WING 옵션표를 의미 기반으로 채운다."""

    payload = [
        {
            "name": str(option.get("name") or f"옵션 {index + 1}"),
            "price": int(option["winner_price"]),
            "stock": DEFAULT_STOCK_QUANTITY,
        }
        for index, option in enumerate(matched)
    ]
    try:
        result = page.evaluate(
            """options => {
                const visible = element => Boolean(
                    element && !element.disabled
                    && (element.offsetWidth || element.offsetHeight
                        || element.getClientRects().length)
                );
                const roots = Array.from(document.querySelectorAll(
                    '#optionPaneTable, [class*="option-pane-table"], '
                    + '[data-component*="option"], [class*="option-price"]'
                )).filter(visible);
                const root = roots.find(element => {
                    const text = (element.innerText || '').replace(/\\s+/g, ' ');
                    return /판매가|아이템\\s*위너|재고/.test(text)
                        || element.querySelector('[data-item-key="salePrice"], '
                            + '[name*="salePrice" i], [id*="salePrice" i]');
                });
                if (!root) return {handled: false, filled: 0, problems: []};

                const allInputs = Array.from(root.querySelectorAll(
                    'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])'
                )).filter(visible);
                const rows = [];
                const seen = new Set();
                for (const input of allInputs) {
                    const row = input.closest(
                        'tr, [role="row"], .option-pane-table-row, '
                        + '[data-item-index], [class*="option-row"], [class*="table-row"]'
                    );
                    if (!row || seen.has(row)) continue;
                    seen.add(row);
                    const fields = Array.from(row.querySelectorAll(
                        'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])'
                    )).filter(visible);
                    const semantic = field => [
                        field.getAttribute('name'), field.id,
                        field.getAttribute('data-item-key'),
                        field.getAttribute('placeholder'),
                        field.getAttribute('aria-label'),
                        field.closest('[data-item-key]')?.getAttribute('data-item-key')
                    ].filter(Boolean).join(' ').toLowerCase();
                    let price = fields.find(field =>
                        /sale.?price|판매가/.test(semantic(field))
                    );
                    let stock = fields.find(field =>
                        /maximum.?buy.?count|stock|inventory|재고/.test(semantic(field))
                    );
                    if ((!price || !stock) && fields.length >= 3) {
                        price = price || fields[1];
                        stock = stock || fields[2];
                    }
                    if (price && stock && price !== stock) {
                        rows.push({row, price, stock});
                    }
                }
                if (!rows.length) {
                    return {handled: false, filled: 0, problems: []};
                }
                if (rows.length < options.length) {
                    return {
                        handled: true,
                        filled: 0,
                        problems: [`옵션행 ${rows.length}/${options.length}`]
                    };
                }
                const setter = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype, 'value'
                ).set;
                const setValue = (field, value) => {
                    setter.call(field, String(value));
                    field.dispatchEvent(new Event('input', {bubbles: true}));
                    field.dispatchEvent(new Event('change', {bubbles: true}));
                    field.dispatchEvent(new Event('blur', {bubbles: true}));
                    return String(field.value || '').replace(/\\D/g, '') === String(value);
                };
                const problems = [];
                let filled = 0;
                options.forEach((option, index) => {
                    const entry = rows[index];
                    const priceOkay = setValue(entry.price, option.price);
                    const stockOkay = setValue(entry.stock, option.stock);
                    if (priceOkay && stockOkay) filled += 1;
                    else problems.push(
                        `${option.name}: 판매가=${entry.price.value}, 재고=${entry.stock.value}`
                    );
                });
                return {handled: true, filled, problems};
            }""",
            payload,
        )
    except Exception:
        return False
    if not result or not result.get("handled"):
        return False
    if int(result.get("filled") or 0) != len(matched):
        problems = ", ".join(str(value) for value in result.get("problems") or [])
        raise WingAutomationError(
            "신형 WING 옵션표의 판매가·재고를 모두 입력하지 못했습니다. "
            f"({problems or '입력행 확인 필요'})"
        )
    page.wait_for_timeout(500)
    return True


def _fill_option_prices_and_stock(page, matched: list[dict]) -> None:
    # 필수 옵션 자동 보완 뒤 WING이 판매가 표를 다시 그리는 경우도 있으므로
    # 입력 직전에 한 번 더 실제 편집기 준비 상태를 확인한다.
    _wait_for_option_price_editor(page, len(matched), timeout_ms=30_000)
    if _fill_easy_option_prices_and_stock(page, matched):
        return
    if _fill_virtual_option_prices_and_stock(page, matched):
        return
    if _fill_generic_option_prices_and_stock(page, matched):
        return
    tables = page.locator("table")
    option_table = None
    for index in range(tables.count()):
        table = tables.nth(index)
        if table.is_visible() and "아이템위너가격" in table.inner_text().replace(" ", ""):
            option_table = table
            break
    if option_table is None:
        # 현재 WING 버전은 옵션 목록을 HTML table이 아닌 div grid로 렌더링한다.
        # 각 옵션은 정상가/판매가/재고/판매자코드/모델/바코드의 빈 입력 6개다.
        candidates = page.locator('input[type="text"][placeholder=""]')
        visible_inputs = [
            candidates.nth(index)
            for index in range(candidates.count())
            if candidates.nth(index).is_visible() and not candidates.nth(index).is_disabled()
        ]
        required = len(matched) * 6
        if len(visible_inputs) < required:
            raise WingAutomationError("옵션 판매가 입력표를 찾지 못했습니다.")
        for index, option in enumerate(matched):
            fields = visible_inputs[index * 6 : (index + 1) * 6]
            _type_react(fields[1], str(option["winner_price"]))
            _type_react(fields[2], str(DEFAULT_STOCK_QUANTITY))
        return

    rows = []
    for index in range(option_table.locator("tr").count()):
        row = option_table.locator("tr").nth(index)
        inputs = row.locator('input[type="text"]')
        if row.is_visible() and inputs.count() >= 3:
            rows.append(row)
    if len(rows) < len(matched):
        raise WingAutomationError("선택 옵션 수와 판매가 입력 행 수가 일치하지 않습니다.")

    for index, option in enumerate(matched):
        inputs = rows[index].locator('input[type="text"]')
        _type_react(inputs.nth(1), str(option["winner_price"]))
        _type_react(inputs.nth(2), str(DEFAULT_STOCK_QUANTITY))


def _disable_auto_price_adjustment(page) -> int:
    """옵션별 자동가격조정 스위치를 모두 끈다.

    카탈로그 매칭 상품은 자동가격조정 영역이 읽기 전용으로 렌더링되기도
    한다. 이 경우에는 이미 비활성 상태이므로 그대로 통과한다.
    """

    option_tables = page.locator("#optionPaneTable:visible")
    if not option_tables.count():
        option_tables = page.locator("#optionPaneTable")
    if not option_tables.count():
        raise WingAutomationError("자동가격조정 옵션표를 찾지 못했습니다.")
    table = option_tables.last
    wrappers = table.locator(".auto-pricing-toggle-btn")
    changed = 0

    for index in range(wrappers.count()):
        wrapper = wrappers.nth(index)
        if not wrapper.is_visible():
            continue
        checkboxes = wrapper.locator('input[type="checkbox"]')
        if checkboxes.count():
            checkbox = checkboxes.last
            if not checkbox.is_checked():
                continue
            if checkbox.is_disabled():
                raise WingAutomationError(
                    "켜진 자동가격조정 항목이 잠겨 있어 비활성화할 수 없습니다."
                )
            labels = wrapper.locator("label")
            (labels.last if labels.count() else checkbox).click(force=True)
            _dismiss_visible_wing_confirmation(page, wait_ms=700)
            page.wait_for_timeout(250)
            if checkbox.is_checked():
                raise WingAutomationError(
                    "자동가격조정 비활성화가 적용되지 않았습니다."
                )
            changed += 1
            continue

        switches = wrapper.locator('[role="switch"], button')
        if not switches.count():
            continue
        switch = switches.last
        if switch.get_attribute("aria-checked") != "true":
            continue
        switch.click(force=True)
        _dismiss_visible_wing_confirmation(page, wait_ms=700)
        page.wait_for_timeout(250)
        if switch.get_attribute("aria-checked") == "true":
            raise WingAutomationError(
                "자동가격조정 비활성화가 적용되지 않았습니다."
            )
        changed += 1

    return changed


def _choose_dropdown_near(
    page,
    label: str | re.Pattern[str],
    desired: re.Pattern[str],
) -> None:
    labels = (
        page.get_by_text(label, exact=True)
        if isinstance(label, str)
        else page.get_by_text(label)
    )
    label_locator = _last_visible(labels)
    container = _ancestor_with(label_locator, "li.init.option", levels=6)
    current = _last_visible(container.locator("li.init.option"))
    current.click()
    option = _last_visible(page.get_by_text(desired))
    option.click()


def _visible_or_none(locator):
    for index in range(locator.count() - 1, -1, -1):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    return None


def _wait_visible_or_none(page, locator, *, timeout_ms: int = 8_000):
    deadline = time.monotonic() + max(timeout_ms, 0) / 1_000
    while time.monotonic() <= deadline:
        candidate = _visible_or_none(locator)
        if candidate is not None:
            return candidate
        page.wait_for_timeout(150)
    return None


def _choose_shipping_fee_type(page, desired: re.Pattern[str]) -> None:
    """쿠팡 배송비 전용 선택기를 우선 사용하고 문구 탐색은 보조로 쓴다."""

    currents = page.locator(
        ".delivery-charge-type-section "
        ".deliveryChargeTypeSelectionWrapper li.init.option, "
        ".deliveryChargeTypeSelectionWrapper li.init.option"
    )
    current = _wait_visible_or_none(page, currents)
    if current is None:
        _choose_dropdown_near(
            page,
            re.compile(r"^\s*배송비\s*(?:종류|유형|방식)?\s*\*?\s*$"),
            desired,
        )
        return

    current.click()
    page.wait_for_timeout(250)
    direct_options = page.locator(
        ".delivery-charge-type-section ul.selection-expand li.option"
    ).filter(has_text=desired)
    option = _wait_visible_or_none(page, direct_options, timeout_ms=2_000)
    if option is None:
        option = _wait_visible_or_none(
            page,
            page.get_by_text(desired),
            timeout_ms=2_000,
        )
    if option is None:
        raise WingAutomationError("WING 배송비 종류의 선택값을 찾지 못했습니다.")
    option.click()
    page.wait_for_timeout(350)


def _ensure_purchase_agent_shipping_method(page) -> bool:
    """배송방법을 먼저 구매대행으로 확정해 하위 배송 필드를 렌더링한다."""

    # 구형/현재 WING 모두 배송방법을 바꾸기 전에는 배송비 종류와 출고
    # 소요일을 렌더링하지 않을 수 있다. 전용 class를 먼저 사용하고,
    # class가 바뀐 빌드는 화면 문구 주변의 선택기를 찾는다.
    scopes = page.locator(
        ".delivery-method-section, .deliveryMethodSelectionWrapper"
    )
    scope = _visible_or_none(scopes)
    if scope is None:
        labels = page.get_by_text(re.compile(r"^\s*배송\s*방법\s*\*?\s*$"))
        label = _visible_or_none(labels)
        if label is not None:
            try:
                scope = _ancestor_with(
                    label,
                    "li.init.option, [role='combobox'], button",
                    levels=9,
                )
            except WingAutomationError:
                scope = None

    if scope is not None:
        current = _visible_or_none(
            scope.locator("li.init.option, [role='combobox']")
        )
        if current is not None:
            try:
                if "구매대행" in " ".join(current.inner_text().split()):
                    return True
            except Exception:
                pass
            current.click()
            page.wait_for_timeout(250)
            purchase_agent = _visible_or_none(
                page.get_by_text("구매대행", exact=True)
            )
            if purchase_agent is not None:
                purchase_agent.click()
                page.wait_for_timeout(700)
                return True

    # 실험형 새 화면은 선택 전 값이 버튼/텍스트로만 표시된다.
    prompts = page.get_by_text(
        re.compile(r"^\s*배송\s*방법.*선택.*$"),
    )
    prompt = _visible_or_none(prompts)
    if prompt is not None:
        prompt.click()
        page.wait_for_timeout(250)
        purchase_agent = _visible_or_none(
            page.get_by_text("구매대행", exact=True)
        )
        if purchase_agent is None:
            raise WingAutomationError("WING 배송방법에서 구매대행을 찾지 못했습니다.")
        purchase_agent.click()
        page.wait_for_timeout(700)
        return True
    return False


def _dismiss_visible_wing_confirmation(page, *, wait_ms: int = 2_500) -> str:
    """늦게 표시된 WING 확인창을 닫고 클릭 가림막이 사라질 때까지 기다린다."""

    deadline = time.monotonic() + max(wait_ms, 0) / 1000
    alert = None
    while time.monotonic() <= deadline:
        alerts = page.locator(".sweet-alert:visible")
        if alerts.count():
            alert = alerts.last
            break
        if wait_ms <= 0:
            break
        page.wait_for_timeout(100)
    if alert is None:
        return ""

    try:
        message = " ".join(alert.inner_text().split())
    except Exception:
        message = ""
    buttons = alert.locator(
        "button.alert-confirm:visible, button.confirm:visible"
    )
    if not buttons.count():
        for name in ("확인", "예", "동의", "적용"):
            candidates = alert.locator("button").filter(
                has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")
            )
            if candidates.count():
                buttons = candidates
                break
    if not buttons.count():
        return message
    buttons.last.click(force=True)

    overlay_deadline = time.monotonic() + 5
    while time.monotonic() < overlay_deadline:
        overlays = page.locator(".sweet-overlay")
        if not any(
            overlays.nth(index).is_visible() for index in range(overlays.count())
        ):
            break
        page.wait_for_timeout(100)
    return message


def _set_product_notice(page) -> None:
    # 대표이미지 방식 전환·업로드 안내창이 WING에서 늦게 나타나면
    # 상품고시정보 드롭다운을 덮는다. 먼저 확인창과 가림막을 정리한다.
    _dismiss_visible_wing_confirmation(page)
    panel = page.locator("#panel-notice-category")
    panel.wait_for(state="visible", timeout=30_000)
    current = _last_visible(panel.locator("li.init.option"))
    current_text = " ".join(current.inner_text().split())
    if current_text in {"", "선택하세요"}:
        _click_reliably(current)
        available = panel.locator("ul.selection-expand li.option")
        option = None
        for pattern in (
            re.compile(r"^\s*기타\s*재화\s*$"),
            re.compile(r"^\s*기타(?!\s*서비스).*$"),
        ):
            preferred = available.filter(has_text=pattern)
            try:
                option = _last_visible(preferred)
                break
            except WingAutomationError:
                continue
        if option is None:
            # 카테고리에 따라 '기타 재화'가 목록에서 제거된 신버전은 WING이
            # 허용한 첫 번째 실제 고시 분류를 사용한다.
            visible_options = []
            for index in range(available.count()):
                candidate = available.nth(index)
                if not candidate.is_visible():
                    continue
                text = " ".join(candidate.inner_text().split())
                if text and text != "선택하세요":
                    visible_options.append(candidate)
            if not visible_options:
                raise WingAutomationError(
                    "해당 카테고리에서 선택 가능한 상품정보제공고시 분류가 없습니다."
                )
            option = visible_options[0]
        _click_reliably(option)
        page.wait_for_timeout(600)

    current = _last_visible(panel.locator("li.init.option"))
    selected_notice = " ".join(current.inner_text().split())
    if selected_notice in {"", "선택하세요"}:
        raise WingAutomationError(
            "상품정보제공고시 분류가 선택되지 않았습니다. WING 화면을 다시 확인하세요."
        )

    checkbox = panel.locator(
        ".notice-category-option-section > span.sc-common-check "
        'input[type="checkbox"]'
    ).first
    if not checkbox.count():
        raise WingAutomationError(
            "상품정보제공고시의 '전체 상품 상세페이지 참조' 항목을 찾지 못했습니다."
        )
    if not checkbox.is_checked():
        checkbox.check(force=True)
        page.wait_for_timeout(300)
    if not checkbox.is_checked():
        raise WingAutomationError(
            "상품정보제공고시의 '전체 상품 상세페이지 참조'가 적용되지 않았습니다."
        )


def _set_shipping_new(page, shipping_fee_type: str) -> None:
    _ensure_purchase_agent_shipping_method(page)

    carrier = page.get_by_text("택배사를 선택해주세요", exact=True)
    if carrier.count():
        _last_visible(carrier).click()
        page.wait_for_timeout(250)
        preferred = page.get_by_text("CJ대한통운", exact=True)
        if preferred.count():
            _last_visible(preferred).click()
        else:
            choices = page.locator('[role="option"]:visible')
            if not choices.count():
                raise WingAutomationError("WING 택배사 선택 목록을 찾지 못했습니다.")
            choices.first.click()
        page.wait_for_timeout(400)

    if shipping_fee_type == "free":
        desired_text = "무료배송"
    elif shipping_fee_type == "cash_on_delivery":
        desired_text = "착불배송"
    else:
        raise WingAutomationError("배송비 방식이 선택되지 않았습니다.")
    fee_option = page.get_by_text(desired_text, exact=True)
    if not fee_option.count() and desired_text == "착불배송":
        fee_option = page.get_by_text("착불", exact=True)
    if not fee_option.count():
        raise WingAutomationError(f"WING의 '{desired_text}' 선택 항목을 찾지 못했습니다.")
    _last_visible(fee_option).click(force=True)
    # 배송비 선택 직후 늦게 나타난 확인창이 다음 입력을 가리는 경우가 있다.
    # 오버레이가 완전히 사라진 뒤 출고 소요일을 입력한다.
    _dismiss_visible_wing_confirmation(page, wait_ms=3_500)
    page.wait_for_timeout(250)

    lead_label = page.get_by_text("출고 소요일", exact=True)
    if not lead_label.count():
        raise WingAutomationError("WING의 출고 소요일 항목을 찾지 못했습니다.")
    section = _ancestor_with(_last_visible(lead_label), "button", minimum=1, levels=8)
    day_buttons = section.locator("button").filter(has_text=re.compile(r"^\s*\d+일\s*$"))
    if not day_buttons.count():
        raise WingAutomationError("WING의 출고 소요일 선택 버튼을 찾지 못했습니다.")
    day_button = _last_visible(day_buttons)
    try:
        day_button.click(timeout=8_000)
    except Exception:
        _dismiss_visible_wing_confirmation(page, wait_ms=3_500)
        day_button.click(force=True)
    page.wait_for_timeout(200)
    desired_day = page.get_by_text(f"{DEFAULT_SHIPPING_LEAD_DAYS}일", exact=True)
    if not desired_day.count():
        raise WingAutomationError(
            f"WING에서 출고 {DEFAULT_SHIPPING_LEAD_DAYS}일을 선택할 수 없습니다."
        )
    try:
        _last_visible(desired_day).click(timeout=8_000)
    except Exception:
        _dismiss_visible_wing_confirmation(page, wait_ms=3_500)
        _last_visible(desired_day).click(force=True)


def _shipping_layout_version(page) -> str:
    """현재 사업자 WING의 배송 입력 화면이 신버전인지 구버전인지 판별한다.

    쿠팡은 사업자별로 두 화면을 동시에 운영한다. 배송방법이 이미
    ``구매대행``으로 선택된 신버전에서는 '배송 방법을 선택' 안내가 사라지므로
    안내 문구가 아니라 실제 배송비 입력 구조를 우선 확인해야 한다.
    """

    legacy_dropdowns = page.locator(
        ".delivery-charge-type-section "
        ".deliveryChargeTypeSelectionWrapper li.init.option, "
        ".deliveryChargeTypeSelectionWrapper li.init.option"
    )
    if _visible_or_none(legacy_dropdowns) is not None:
        return "legacy"

    new_fee_heading = page.get_by_text("기본 배송비", exact=True)
    if _visible_or_none(new_fee_heading) is not None:
        return "new"

    new_method_prompt = page.get_by_text(
        re.compile(r"^\s*배송\s*방법.*선택.*$")
    )
    if _visible_or_none(new_method_prompt) is not None:
        return "new"

    # 알 수 없는 화면은 기존 드롭다운 탐색을 유지한다. 구버전의 클래스가
    # 일부 달라도 문구 기반 보조 탐색이 한 번 더 동작하게 하기 위함이다.
    return "legacy"


def _ensure_remote_area_shipping_available(page) -> None:
    """제주/도서산간 배송 가능을 명시적으로 선택한다.

    WING은 판매자 주소와 배송방법을 바꿀 때 이 값을 이전 기본값으로
    되돌릴 수 있다. 화면 기본값에 의존하지 않고 등록 준비와 최종 저장
    직전에 모두 다시 확인한다.
    """

    heading_pattern = re.compile(
        r"^\s*제주(?:/|·|\s)*(?:및\s*)?도서산간.*배송.*$"
    )
    headings = page.get_by_text(heading_pattern)
    visible_headings = [
        headings.nth(index)
        for index in range(headings.count())
        if headings.nth(index).is_visible()
    ]
    if not visible_headings:
        raise WingAutomationError(
            "WING의 제주/도서산간 배송 가능 여부 항목을 찾지 못했습니다."
        )

    heading = visible_headings[-1]
    scope = None
    current = heading
    for _ in range(8):
        current = current.locator("xpath=..")
        available = current.get_by_text("가능", exact=True)
        unavailable = current.get_by_text("불가능", exact=True)
        if any(
            available.nth(index).is_visible()
            for index in range(available.count())
        ) and any(
            unavailable.nth(index).is_visible()
            for index in range(unavailable.count())
        ):
            scope = current
            break
    if scope is None:
        raise WingAutomationError(
            "제주/도서산간 배송 가능 선택항목의 화면 구조를 확인하지 못했습니다."
        )

    available = _last_visible(scope.get_by_text("가능", exact=True))
    available_label = available.locator("xpath=ancestor-or-self::label[1]")
    click_target = available_label.last if available_label.count() else available

    radio = None
    if available_label.count():
        for_id = available_label.last.get_attribute("for") or ""
        if for_id:
            linked = page.locator(f'input[type="radio"]#{for_id}')
            if linked.count():
                radio = linked.last
        if radio is None:
            nested = available_label.last.locator('input[type="radio"]')
            if nested.count():
                radio = nested.last

    selected = bool(radio is not None and radio.is_checked())
    if not selected:
        uicfg = click_target.get_attribute("data-sc-common-uicfg") or ""
        classes = click_target.get_attribute("class") or ""
        selected = (
            "type:primary" in uicfg
            or click_target.get_attribute("aria-checked") == "true"
            or any(token in classes.split() for token in ("active", "checked", "selected"))
        )

    if not selected:
        click_target.click(force=True)
        page.wait_for_timeout(350)

    if radio is not None and not radio.is_checked():
        raise WingAutomationError(
            "제주/도서산간 배송 가능 설정이 적용되지 않았습니다."
        )


def _set_shipping(page, shipping_fee_type: str) -> None:
    # 사업자마다 신·구 배송 화면이 다르며, 같은 사업자도 구매대행 선택 후
    # 안내 문구가 사라진다. 먼저 배송방법을 확정해 실제 입력란을 렌더링한 뒤
    # 표시된 구조를 기준으로 버전을 판별한다.
    _ensure_purchase_agent_shipping_method(page)
    page.wait_for_timeout(400)
    if _shipping_layout_version(page) == "new":
        _set_shipping_new(page, shipping_fee_type)
    else:
        if shipping_fee_type == "free":
            desired = re.compile(r"^무료배송$")
        elif shipping_fee_type == "cash_on_delivery":
            desired = re.compile(r"^착불(?:배송)?$")
        else:
            raise WingAutomationError("배송비 방식이 선택되지 않았습니다.")
        _choose_shipping_fee_type(page, desired)

        lead_label = _last_visible(
            page.get_by_text(re.compile(r"^\s*출고\s*소요일\s*\*?\s*$"))
        )
        lead_input = lead_label.locator('xpath=following::input[@type="number"][1]')
        _type_react(lead_input, str(DEFAULT_SHIPPING_LEAD_DAYS))

    _ensure_remote_area_shipping_available(page)


def _select_saved_addresses_if_needed(page) -> list[str]:
    """새 WING 폼에서 비어 있는 출고지/반품지를 첫 저장 주소로 채운다."""

    body = " ".join(page.locator("body").inner_text().split())
    needs = []
    if "선택된 출고지 없음" in body:
        needs.append((0, "출고지"))
    if "반품지를 등록" in body or "선택된 반품지 없음" in body:
        needs.append((1, "반품지"))
    if not needs:
        return []

    applied: list[str] = []
    for ordinal, label in needs:
        address_buttons = [
            page.get_by_text("판매자 주소록", exact=True).nth(index)
            for index in range(page.get_by_text("판매자 주소록", exact=True).count())
            if page.get_by_text("판매자 주소록", exact=True).nth(index).is_visible()
        ]
        if ordinal >= len(address_buttons):
            raise WingAutomationError(f"{label} 판매자 주소록 버튼을 찾지 못했습니다.")
        address_buttons[ordinal].click()
        page.wait_for_timeout(350)
        widgets = page.locator('div[data-wuic-partial="widget"]:visible')
        if not widgets.count():
            raise WingAutomationError(f"{label} 주소 선택창이 열리지 않았습니다.")
        widget = widgets.last
        select_buttons = widget.locator("button").filter(
            has_text=re.compile(r"^\s*선택\s*$")
        )
        if not select_buttons.count():
            raise WingAutomationError(f"저장된 {label} 주소가 없습니다.")
        _last_visible(select_buttons).click(force=True)
        page.wait_for_timeout(500)
        applied.append(f"{label} 자동 선택")
    return applied


def _return_fee_field(page):
    labels = page.get_by_text("반품배송비(편도)", exact=True)
    for label_index in range(labels.count() - 1, -1, -1):
        label = labels.nth(label_index)
        if not label.is_visible():
            continue
        # 사업자별 신·구 WING은 입력칸을 form-section의 직계/간접 자식으로
        # 서로 다르게 배치한다. 가까운 부모부터 올라가며 표시된 입력칸을 찾는다.
        container = label
        for _level in range(8):
            container = container.locator("xpath=..")
            fields = container.locator('input[type="text"]')
            for field_index in range(fields.count() - 1, -1, -1):
                field = fields.nth(field_index)
                if field.is_visible() and not field.is_disabled():
                    return field

        following = label.locator(
            'xpath=following::input[@type="text" and not(@disabled)][1]'
        )
        if following.count() and following.first.is_visible():
            return following.first
    raise WingAutomationError("WING의 반품배송비 입력칸을 찾지 못했습니다.")


def _parse_money_input(value: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", value or "")
    return int(digits) if digits else None


_SUBMISSION_DIAGNOSTIC_NOISE = frozenset(
    {
        "이미지 URL 입력",
        "기본 입력",
        "구매 옵션별로 입력",
        "필수표기정보 보기",
        "반품배송비(편도) *",
    }
)


def _submission_diagnostic_messages(
    body: str,
    alert_messages: Sequence[str],
) -> list[str]:
    """등록 실패 원인만 추려 사용자에게 보여줄 짧은 목록을 만든다."""

    priority: list[str] = []
    general: list[str] = []
    seen: set[str] = set()

    def add(raw: str, *, is_alert: bool = False) -> None:
        message = " ".join(str(raw or "").split())
        if not message or message in _SUBMISSION_DIAGNOSTIC_NOISE or message in seen:
            return
        tokens = ("필수", "낮아야", "확인해주세요", "오류", "반품배송비")
        if not any(token in message for token in tokens) and not (
            is_alert and "입력" in message
        ):
            return
        seen.add(message)
        (priority if "반품배송비" in message else general).append(message)

    for message in alert_messages:
        add(message, is_alert=True)
    for line in (body or "").splitlines():
        add(line)

    # 반품비 안내는 반드시 남기고, 나머지는 화면 아래쪽의 최신 오류를 사용한다.
    remaining = max(5 - len(priority), 0)
    return (priority[:5] + general[-remaining:])[:5]


def _set_return_fee_value(page, target: int) -> int:
    """반품비를 입력한 뒤 WING이 실제로 유지한 값까지 검증한다."""

    last_value: int | None = None
    for _attempt in range(3):
        try:
            field = _return_fee_field(page)
            # click과 fill 사이에 React가 입력칸을 다시 그리면 기존 locator가
            # 사라진다. 한 번의 DOM 실행으로 값과 input/change 이벤트를 함께
            # 전달하고, 검증할 때 입력칸을 새로 찾는다.
            field.evaluate(
                """(element, value) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(element, value);
                    element.dispatchEvent(new Event('input', {bubbles: true}));
                    element.dispatchEvent(new Event('change', {bubbles: true}));
                    element.blur();
                }""",
                str(target),
            )
            page.wait_for_timeout(450)
            verified_field = _return_fee_field(page)
            last_value = _parse_money_input(verified_field.input_value(timeout=3_000))
            if last_value == target:
                return target
        except Exception:
            page.wait_for_timeout(350)
    raise WingAutomationError(
        "반품배송비 입력값이 WING에 반영되지 않았습니다. "
        f"요청 {target:,}원 / 화면 {last_value if last_value is not None else '확인 불가'}원"
    )


def _set_return_fee(page, minimum_price: int) -> int:
    target = choose_return_fee(minimum_price)
    _set_return_fee_value(page, target)
    return target


def _extract_country(text: str) -> str:
    patterns = (
        r"(?:제조국|원산지)(?:\([^)]*\))?\s*[:：]?\s*(중국|대한민국|한국|일본|미국|독일|프랑스|베트남|대만)",
        r"(중국산|국산|한국산|일본산|미국산|독일산|베트남산|대만산)",
    )
    mapping = {"국산": "대한민국", "한국": "대한민국", "한국산": "대한민국"}
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            value = match.group(1)
            value = value[:-1] if value.endswith("산") and value != "국산" else value
            return mapping.get(value, value)
    return ""


PURCHASE_AGENT_CERTIFICATION_TEXT = re.compile(
    r"구매대행으로\s*구매\s*가능한\s*품목|구매대행.*가능.*품목|"
    r"구매대행.*(?:KC|인증)|(?:KC|인증).*구매대행"
)


def _ensure_purchase_agent_certification(page) -> bool:
    """표시된 경우 '구매대행으로 구매가능한 품목'을 선택한다."""

    selectors = (
        "#certificationType_PURCHASED_WITHOUT_KC_MARK_4",
        'input[id*="PURCHASED_WITHOUT_KC_MARK"]',
        'input[value*="PURCHASED_WITHOUT_KC_MARK"]',
    )
    for selector in selectors:
        options = page.locator(selector)
        for index in range(options.count() - 1, -1, -1):
            option = options.nth(index)
            try:
                option.check(force=True)
                page.wait_for_timeout(250)
                if option.is_checked():
                    return True
            except Exception:
                continue

    # WING이 입력 ID를 바꾸는 경우에도 '구매대행' 문구가 붙은 라디오의
    # for 속성을 따라간다. 인증번호를 임의로 만드는 선택지는 누르지 않는다.
    labels = page.locator("label").filter(has_text=PURCHASE_AGENT_CERTIFICATION_TEXT)
    for index in range(labels.count() - 1, -1, -1):
        label = labels.nth(index)
        target_id = label.get_attribute("for") or ""
        option = (
            page.locator(f'input[id="{target_id}"]')
            if target_id
            else label.locator('input[type="radio"], input[type="checkbox"]')
        )
        try:
            if option.count():
                option.last.check(force=True)
            else:
                # 일부 WING 컴포넌트는 실제 input을 별도 위치에 두고 label
                # 클릭으로만 상태를 바꾼다.
                label.click(force=True)
            page.wait_for_timeout(250)
            if not option.count() or option.last.is_checked():
                return True
        except Exception:
            continue

    # label 태그가 아닌 사용자 정의 라디오 컴포넌트도 정확한 화면 문구로
    # 선택한다. 같은 라디오를 다시 눌러도 선택이 해제되지 않는다.
    text_options = page.get_by_text(PURCHASE_AGENT_CERTIFICATION_TEXT)
    for index in range(text_options.count() - 1, -1, -1):
        option_text = text_options.nth(index)
        try:
            if not option_text.is_visible():
                continue
            option_text.click(force=True)
            page.wait_for_timeout(250)
            return True
        except Exception:
            continue

    # 인증 선택 자체가 없는 카테고리도 있다. 이때는 프로그램이 임의로
    # 실패시키지 않고 계속 진행하며, 실제 필수 여부는 WING 저장 검증이
    # 최종 판단하도록 둔다.
    return False


def _ensure_product_meta_defaults(page) -> list[str]:
    """상품 주요 정보에서 아무 값도 선택되지 않은 필수 라디오를 보완한다."""

    choices = (
        (
            "병행수입 아님",
            (
                "#undefined_NOT_PARALLEL_IMPORTED_1",
                'input[value="NOT_PARALLEL_IMPORTED"]',
            ),
        ),
        (
            "전체 연령",
            ("#undefined_EVERYONE_0", 'input[value="EVERYONE"]'),
        ),
        (
            "인당 최대구매수량 설정안함",
            ("#setMaximumBuyForPerson_N_1", 'input[name="setMaximumBuyForPerson"][value="N"]'),
        ),
        (
            "판매기간 설정안함",
            ("#salePeriod_N_1", 'input[name="salePeriod"][value="N"]'),
        ),
        (
            "부가세 과세",
            ("#undefined_TAX_0", 'input[value="TAX"]'),
        ),
    )
    applied: list[str] = []
    for label, selectors in choices:
        target = None
        for selector in selectors:
            candidates = page.locator(selector)
            target = _visible_or_none(candidates)
            if target is not None:
                break
        if target is None:
            continue
        try:
            wrapper = target.locator(
                "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' under_line ')][1]"
            )
            checked = wrapper.locator('input[type="radio"]:checked') if wrapper.count() else None
            if checked is not None and checked.count():
                continue
            target.check(force=True)
            page.wait_for_timeout(180)
            if target.is_checked():
                applied.append(label)
        except Exception:
            continue
    return applied


def _product_meta_missing_fields(page) -> list[str]:
    """상품 주요 정보 영역에서 WING이 비워 둔 필수 행 이름을 읽는다."""

    try:
        values = page.evaluate(
            """() => {
                const panel = document.querySelector('#panel-product-meta-info');
                if (!panel) return [];
                const result = [];
                const push = value => {
                    value = String(value || '').replace(/\\s+/g, ' ').trim();
                    if (value && !result.includes(value)) result.push(value);
                };
                for (const row of panel.querySelectorAll('.wrapper.under_line')) {
                    if (!row.querySelector('.required-mark')) continue;
                    const label = row.querySelector('.label-wrap');
                    const name = label && label.innerText;
                    const radios = Array.from(row.querySelectorAll('input[type="radio"]'));
                    if (radios.length && !radios.some(input => input.checked)) {
                        push(name);
                        continue;
                    }
                    const fields = Array.from(row.querySelectorAll(
                        'input[type="text"], input[type="number"], textarea, select'
                    )).filter(field => !field.disabled);
                    if (fields.length && fields.every(field => !String(field.value || '').trim())) {
                        push(name);
                    }
                }
                for (const error of panel.querySelectorAll(
                    '.error-message, .validation-message, .input-error, [class*="error-text"]'
                )) {
                    if (error.offsetParent !== null) push(error.innerText);
                }
                return result;
            }"""
        )
        return [str(value) for value in values if str(value).strip()]
    except Exception:
        return []


def _create_temporary_inventory_id(page) -> str:
    """필수정보 입력 후 중간저장하여 옵션 매칭용 inventoryId를 만든다."""

    buttons = page.get_by_role("button", name="중간저장", exact=True)
    button = _visible_or_none(buttons)
    if button is None:
        raise WingAutomationError(
            "쿠팡 임시 상품 ID 생성을 위한 중간저장 버튼을 찾지 못했습니다."
        )
    _click_reliably(button)
    deadline = time.monotonic() + 8
    alert_text = ""
    while time.monotonic() < deadline:
        alerts = page.locator(".sweet-alert:visible")
        if alerts.count():
            alert_text = " ".join(alerts.last.inner_text().split())
            break
        page.wait_for_timeout(150)
    if alert_text:
        _dismiss_visible_wing_confirmation(page, wait_ms=0)
        failure_tokens = ("입력항목", "확인해주세요", "필수", "오류")
        if any(token in alert_text for token in failure_tokens):
            raise WingAutomationError(
                "필수정보 입력 후에도 임시 상품 ID를 만들지 못했습니다: "
                f"{alert_text[:500]}"
            )
    page.wait_for_timeout(1_200)
    # 중간저장은 서버에 inventoryId를 만들지만 새 WING의 옵션 매칭
    # 컴포넌트는 생성 전의 null ID를 계속 들고 있을 수 있다. 저장된 폼을
    # 한 번 다시 읽어 Vue 상태에도 새 ID가 반영되게 한다.
    try:
        page.reload(wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1_500)
        page.locator("#panel-product-meta-info").wait_for(
            state="attached", timeout=30_000
        )
    except Exception as exc:
        raise WingAutomationError(
            "임시 상품 ID 생성 후 저장된 WING 화면을 다시 불러오지 못했습니다."
        ) from exc
    return alert_text


def _safe_suffix(url: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp"} else ".jpg"


def _normalize_coupang_image_url(url: str) -> str:
    value = (url or "").strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http://") and "coupangcdn.com" in value:
        return "https://" + value.removeprefix("http://")
    return value


def _original_coupang_image_url(url: str) -> str:
    """쿠팡 썸네일 주소를 가능한 경우 원본 CDN 주소로 바꾼다."""

    value = _normalize_coupang_image_url(url)
    marker = "/thumbnails/remote/"
    if not re.search(r"//thumbnail\d*\.coupangcdn\.com", value) or marker not in value:
        return value
    remainder = value.split(marker, 1)[1]
    if "/" not in remainder:
        return value
    original_path = remainder.split("/", 1)[1]
    return f"https://image.coupangcdn.com/{original_path}"


def _make_square_product_image(source: Path, destination: Path) -> str:
    """대표이미지를 WING 권장 규격인 1,000px 정사각형으로 만든다."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        image = opened.convert("RGBA")
        ratio = min(1_000 / image.width, 1_000 / image.height)
        size = (
            max(1, round(image.width * ratio)),
            max(1, round(image.height * ratio)),
        )
        if image.size != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (1_000, 1_000), "white")
        canvas.paste(
            image,
            ((1_000 - size[0]) // 2, (1_000 - size[1]) // 2),
            image.getchannel("A"),
        )
        canvas.save(destination, format="JPEG", quality=95, optimize=True)
        canvas.close()
        image.close()
    return str(destination)


def _cached_detail_main_candidate(output_dir: Path, prepared: Path) -> str:
    """대표 메타이미지가 없을 때 판매자 안내를 뺀 첫 상세 제품이미지를 쓴다."""

    source_dir = output_dir.parent / "source"
    candidates = sorted(
        str(path)
        for path in source_dir.glob("detail_*")
        if path.is_file()
    )
    kept, _removed = filter_known_seller_pages(candidates)
    for candidate in kept:
        try:
            return _make_square_product_image(Path(candidate), prepared)
        except (OSError, ValueError):
            continue
    return ""


def _valid_image_file(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 500:
            return False
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def _download_source_main_image(context, job: dict, output_dir: Path) -> str:
    """원상품의 대표이미지를 내려받고 재시도에서는 저장본을 재사용한다."""

    prepared = output_dir / "main_1000.jpg"
    if prepared.is_file():
        return str(prepared)

    source_page = context.new_page()
    try:
        source_page.goto(
            job["source_url"], wait_until="domcontentloaded", timeout=60_000
        )
        source_page.wait_for_timeout(2_500)
        output_dir.mkdir(parents=True, exist_ok=True)
        candidates: list[str] = []
        dom_images: list[object] = []

        metas = source_page.locator(
            'meta[property="og:image"], meta[name="twitter:image"], '
            'meta[property="twitter:image"]'
        )
        for index in range(metas.count()):
            value = _original_coupang_image_url(
                metas.nth(index).get_attribute("content") or ""
            )
            if value.startswith("http") and value not in candidates:
                candidates.append(value)
        image_links = source_page.locator('link[rel="image_src"]')
        for index in range(image_links.count()):
            value = _original_coupang_image_url(
                image_links.nth(index).get_attribute("href") or ""
            )
            if value.startswith("http") and value not in candidates:
                candidates.append(value)

        # 일부 상품은 og:image가 없고 실제 대표이미지 엘리먼트에만 원본
        # 주소를 보관한다. 대표 영역을 우선하고 CDN 이미지 전체를 후순위로 본다.
        for selector in (
            "#repImageContainer img",
            ".prod-image__detail",
            ".prod-image img",
            'img[src*="coupangcdn.com"]',
            'img[data-src*="coupangcdn.com"]',
        ):
            images = source_page.locator(selector)
            for index in range(min(images.count(), 40)):
                image = images.nth(index)
                values = (
                    image.get_attribute("data-origin"),
                    image.get_attribute("data-zoom-image"),
                    image.get_attribute("src"),
                    image.get_attribute("data-src"),
                )
                for raw in values:
                    value = _original_coupang_image_url(raw or "")
                    if (
                        value.startswith("http")
                        and "coupangcdn.com" in value
                        and value not in candidates
                    ):
                        candidates.append(value)
                dom_images.append(image)
            if dom_images and selector != 'img[src*="coupangcdn.com"]':
                break

        for candidate_index, image_url in enumerate(candidates, start=1):
            downloaded = output_dir / (
                f"main_source_{candidate_index:02d}{_safe_suffix(image_url)}"
            )
            try:
                response = context.request.get(image_url, timeout=20_000)
                if response.ok:
                    downloaded.write_bytes(response.body())
                if _valid_image_file(downloaded):
                    return _make_square_product_image(downloaded, prepared)
                if downloaded.exists():
                    downloaded.unlink()
            except Exception:
                if downloaded.exists() and not _valid_image_file(downloaded):
                    downloaded.unlink()

        # CDN 직접 요청이 차단돼도 브라우저에 렌더링된 대표이미지는 캡처한다.
        for index, image in enumerate(dom_images[:15], start=1):
            try:
                if not image.is_visible():
                    continue
                bounds = image.bounding_box() or {}
                if int(bounds.get("width") or 0) < 120:
                    continue
                downloaded = output_dir / f"main_rendered_{index:02d}.png"
                image.screenshot(path=str(downloaded), timeout=10_000)
                if _valid_image_file(downloaded):
                    return _make_square_product_image(downloaded, prepared)
            except Exception:
                continue

        # 대표 전용 주소가 전혀 없는 상품은 상세 첫 제품 이미지를 미리
        # 내려받아 대표이미지로 사용한다. 상세 단계에서도 같은 파일을 재사용한다.
        source_dir = output_dir.parent / "source"
        _download_source_details(context, job, source_dir)
        return _cached_detail_main_candidate(output_dir, prepared)
    except Exception:
        if prepared.is_file():
            return str(prepared)
        cached = _cached_detail_main_candidate(output_dir, prepared)
        if cached:
            return cached
        try:
            _download_source_details(context, job, output_dir.parent / "source")
        except Exception:
            pass
        return _cached_detail_main_candidate(output_dir, prepared)
    finally:
        source_page.close()


def _download_option_images(
    context,
    matched: Sequence[dict],
    *,
    fallback_main: str,
    output_dir: Path,
) -> list[str]:
    """카탈로그 옵션 카드의 이미지를 옵션 순서대로 내려받는다."""

    output_dir.mkdir(parents=True, exist_ok=True)
    prepared_paths: list[str] = []
    for index, option in enumerate(matched, start=1):
        image_url = _original_coupang_image_url(
            str(option.get("image_url") or "")
        )
        prepared = output_dir / f"option_{index:03d}_1000.jpg"
        source = output_dir / f"option_{index:03d}_source{_safe_suffix(image_url)}"
        if prepared.is_file() and _valid_image_file(prepared):
            prepared_paths.append(str(prepared))
            continue

        if image_url:
            try:
                response = context.request.get(image_url, timeout=20_000)
                if response.ok:
                    source.write_bytes(response.body())
            except Exception:
                pass
        if _valid_image_file(source):
            try:
                prepared_paths.append(
                    _make_square_product_image(source, prepared)
                )
                continue
            except (OSError, ValueError):
                pass

        # 옵션 카드 이미지가 없거나 CDN 요청이 막힌 옵션만 대표이미지를
        # 대체값으로 쓴다. 다른 옵션의 이미지를 임의로 복사하지 않는다.
        prepared_paths.append(fallback_main)
    return prepared_paths


def _align_option_image_paths(
    paths: Sequence[str], *, option_count: int, fallback_main: str
) -> list[str]:
    existing = [str(Path(value)) for value in paths if Path(value).is_file()]
    return [
        existing[index] if index < len(existing) else fallback_main
        for index in range(option_count)
    ]


def _set_dropzone_hidden_input_files(page, upload_scope, path: str) -> bool:
    """Dropzone의 현재 hidden input에 파일을 전달하고 이후 DOM은 다시 만지지 않는다."""

    token = f"codex-main-upload-{time.time_ns()}"
    marked = upload_scope.evaluate(
        """(element, token) => {
            const input = element.dropzone && element.dropzone.hiddenFileInput;
            if (!input) return false;
            input.setAttribute('data-codex-upload-token', token);
            return true;
        }""",
        token,
    )
    if not marked:
        return False
    direct_input = page.locator(f'input[data-codex-upload-token="{token}"]')
    direct_input.set_input_files(path)
    # 파일 전달 직후 Dropzone/React가 input을 제거·교체할 수 있다. 이 시점
    # 이후 같은 locator를 다시 사용하면 업로드가 시작됐는데도 30초 동안
    # 사라진 요소를 기다리므로 여기서 즉시 반환한다.
    return True


def _upload_option_product_images(
    page,
    matched: list[dict],
    option_paths: Sequence[str],
) -> None:
    """WING의 옵션별 등록 탭에서 각 옵션 대표이미지를 개별 업로드한다."""

    if not matched:
        raise WingAutomationError("옵션별 이미지를 연결할 카탈로그 옵션이 없습니다.")
    paths = _align_option_image_paths(
        option_paths,
        option_count=len(matched),
        fallback_main=str(option_paths[0]) if option_paths else "",
    )
    if len(paths) != len(matched) or any(not Path(path).is_file() for path in paths):
        raise WingAutomationError("옵션별 등록에 사용할 이미지가 준비되지 않았습니다.")

    option_label = page.locator('label[for="tab-product-image-pane-1"]')
    if not option_label.count():
        raise WingAutomationError("WING의 '옵션별 등록' 상품이미지 탭을 찾지 못했습니다.")
    option_label.last.dispatch_event("click")
    page.wait_for_timeout(350)
    _dismiss_visible_wing_confirmation(page, wait_ms=2_000)
    option_radio = page.locator("#tab-product-image-pane-1")
    if option_radio.count() and not option_radio.last.is_checked():
        option_label.last.dispatch_event("click")
        page.wait_for_timeout(350)

    records = [dict(option, image_path=paths[index]) for index, option in enumerate(matched)]
    expected = len(records)
    filled: set[str] = set()
    table = page.locator("#imageTable")
    table.last.wait_for(state="visible", timeout=20_000)
    scroll_token = f"codex-option-scroll-{time.time_ns()}"
    table.last.evaluate(
        """(table, token) => {
            const candidates = [];
            const virtual = table.querySelector('.product-image-virtual-list');
            if (virtual) candidates.push(virtual);
            let current = table;
            while (current && current !== document.body) {
                candidates.push(current);
                current = current.parentElement;
            }
            const target = candidates.sort((a, b) =>
                (b.scrollHeight - b.clientHeight) -
                (a.scrollHeight - a.clientHeight)
            )[0] || table;
            target.setAttribute('data-codex-option-scroll', token);
        }""",
        scroll_token,
    )
    scroll_target = page.locator(
        f'[data-codex-option-scroll="{scroll_token}"]'
    ).last
    stagnant = 0

    maximum_passes = max(24, expected * 4)
    for _pass in range(maximum_passes):
        count_before = len(filled)
        rows = page.locator(".product-image-table-row")
        for row_index in range(rows.count()):
            row = rows.nth(row_index)
            if not row.is_visible():
                continue
            name_cell = row.locator(".product-image-table-cell--name")
            row_name = (
                " ".join(name_cell.inner_text().split())
                if name_cell.count()
                else ""
            )
            record = _matched_option_record(row_name, records)
            uploader_scope = row.locator(".item-rep-cell [data-item-index]")
            item_index = -1
            if uploader_scope.count():
                try:
                    item_index = int(
                        uploader_scope.first.get_attribute("data-item-index") or -1
                    )
                except ValueError:
                    item_index = -1
            if record is None and 0 <= item_index < len(records):
                record = records[item_index]
            if record is None:
                continue
            key = (
                f"index:{item_index}"
                if 0 <= item_index < expected
                else _normalize_option_identity(str(record.get("name") or row_name))
            )
            if not key or key in filled:
                continue

            dropzones = row.locator(".item-rep-cell .customdropzone")
            if not dropzones.count():
                continue
            dropzone = dropzones.last
            # 기본 등록 이미지가 모든 행에 복사돼 있으면 먼저 해당 행만 비운다.
            dropzone.evaluate(
                """element => {
                    if (!element.dropzone) return false;
                    element.dropzone.removeAllFiles(true);
                    return true;
                }"""
            )
            page.wait_for_timeout(150)
            if not _set_dropzone_hidden_input_files(
                page, dropzone, str(record["image_path"])
            ):
                raise WingAutomationError(
                    f"옵션 '{record.get('name')}' 이미지 입력칸을 찾지 못했습니다."
                )

            deadline = time.monotonic() + 45
            completed = False
            while time.monotonic() < deadline:
                finalized = dropzone.locator('img[finalizedimageurl]')
                success = dropzone.locator(
                    ".dz-preview.dz-success, .dz-preview.dz-complete"
                )
                if finalized.count() or success.count():
                    completed = True
                    break
                error = dropzone.locator(".dz-preview.dz-error")
                if error.count():
                    break
                page.wait_for_timeout(300)
            if not completed:
                raise WingAutomationError(
                    f"옵션 '{record.get('name')}' 대표이미지 업로드가 완료되지 않았습니다."
                )
            filled.add(key)

        if len(filled) >= expected:
            try:
                scroll_target.evaluate("element => { element.scrollTop = 0; }")
            except Exception:
                pass
            return
        stagnant = stagnant + 1 if len(filled) == count_before else 0
        if stagnant >= 10:
            break
        try:
            scroll_target.evaluate(
                """element => {
                    const maximum = Math.max(0, element.scrollHeight - element.clientHeight);
                    const step = Math.max(240, Math.round(element.clientHeight * 0.75));
                    element.scrollTop = Math.min(maximum, element.scrollTop + step);
                    element.dispatchEvent(new Event('scroll', {bubbles: true}));
                }"""
            )
        except Exception:
            pass
        page.wait_for_timeout(500)

    raise WingAutomationError(
        f"옵션별 대표이미지 {expected}개 중 {len(filled)}개만 등록했습니다."
    )


def _upload_product_main_image(page, path: str) -> None:
    """상품이미지 기본 등록의 대표이미지를 채운다."""

    if not path or not Path(path).is_file():
        raise WingAutomationError(
            "원상품 대표이미지를 가져오지 못해 상품이미지 입력을 중단했습니다."
        )

    basic_label = page.locator('label[for="tab-product-image-pane-0"]')
    if basic_label.count():
        basic_label.last.dispatch_event("click")
        page.wait_for_timeout(300)
        confirmation = page.locator(
            "button.alert-confirm:visible, .sweet-alert:visible button.confirm"
        )
        if confirmation.count():
            confirmation.last.click(force=True)
            page.wait_for_timeout(500)
        basic_radio = page.locator("#tab-product-image-pane-0")
        if basic_radio.count() and not basic_radio.is_checked():
            basic_label.last.dispatch_event("click")
            page.wait_for_timeout(300)

    main_label = _last_visible(page.get_by_text("대표이미지", exact=True))
    main_dropzone = page.locator(
        ".element-main-uploader .customdropzone:visible"
    )
    upload_scope = None
    if main_dropzone.count():
        upload_scope = main_dropzone.last
        # Dropzone이 가진 숨은 파일 입력칸에 직접 파일을 전달한다. 화면의
        # 업로드 영역을 클릭하지 않으므로 Windows '파일 열기' 창이 뜨지 않는다.
        upload_started = False
        upload_deadline = time.monotonic() + 5
        while time.monotonic() < upload_deadline:
            if _set_dropzone_hidden_input_files(page, upload_scope, path):
                upload_started = True
                break
            fallback_inputs = upload_scope.locator('input[type="file"]')
            if fallback_inputs.count():
                fallback_inputs.last.set_input_files(path)
                upload_started = True
                break
            page.wait_for_timeout(200)
        if not upload_started:
            raise WingAutomationError(
                "대표이미지 업로드 입력칸을 찾지 못했습니다. "
                "Windows 파일 선택창은 열지 않고 이 상품을 중단했습니다."
            )
    else:
        # 구형 WING 화면의 대표이미지 입력 방식.
        main_input = main_label.locator(
            'xpath=following::input[@type="file"][1]'
        )
        main_input.set_input_files(path)

    error = page.get_by_text("대표이미지 항목을 입력해 주세요.", exact=True)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        if upload_scope is not None:
            completed = upload_scope.locator(
                ".dz-preview.dz-success, .dz-preview.dz-complete"
            )
            if completed.count():
                return
            upload_error = upload_scope.locator(".dz-preview.dz-error")
            if upload_error.count():
                raise WingAutomationError("대표이미지 업로드를 WING이 거부했습니다.")
            continue
        if not any(
            error.nth(index).is_visible() for index in range(error.count())
        ):
            return
    raise WingAutomationError("대표이미지 업로드가 45초 안에 완료되지 않았습니다.")


def _capture_rendered_detail_page(source_page, output_dir: Path) -> list[str]:
    """상품 상세 DOM을 글자·표·이미지 배치 그대로 여러 장으로 캡처한다."""

    token = f"codex-detail-{time.time_ns()}"
    region = source_page.evaluate(
        """token => {
            const selectors = [
                '.product-detail-content-inside',
                '#productDetail',
                '.product-detail-content',
                '.product-detail',
                '#productDescription',
                '.product-description',
                '.product-detail-seemore-content',
                '.product-detail-seemore',
                '[data-component*="product-detail"]',
                '[class*="productDetail"]',
                '[id*="productDetail"]'
            ];
            const candidates = [];
            const seen = new Set();
            for (const selector of selectors) {
                for (const element of document.querySelectorAll(selector)) {
                    if (seen.has(element)) continue;
                    seen.add(element);
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    if (
                        style.display === 'none' || style.visibility === 'hidden' ||
                        rect.width < 300 || Math.max(rect.height, element.scrollHeight) < 150
                    ) continue;
                    const textLength = (element.innerText || '').trim().length;
                    const imageCount = element.querySelectorAll('img, picture, table').length;
                    const height = Math.max(rect.height, element.scrollHeight);
                    // 상세 전용 안쪽 컨테이너를 우선하고, 같은 종류에서는 실제
                    // 내용(텍스트·이미지)이 많은 영역을 선택한다.
                    const priority = selector === '.product-detail-content-inside' ? 4
                        : selector === '#productDetail' ? 3
                        : selector === '.product-detail-content' ? 2 : 1;
                    candidates.push({element, priority, textLength, imageCount, height});
                }
                if (candidates.some(item => item.priority >= 3)) break;
            }
            // 클래스명이 완전히 바뀐 화면에서는 상세영역의 큰 이미지들을
            // 기준으로 공통 부모를 찾는다. 추천/연관상품 링크 속 이미지는
            // 제외하므로 다른 상품 상세가 섞이지 않는다.
            if (!candidates.length) {
                const excluded = /review|recommend|related|similar|carousel|thumbnail|search|ranking/i;
                const detailHeadings = Array.from(document.querySelectorAll(
                    'h1, h2, h3, h4, button, a, [role="tab"]'
                )).filter(element =>
                    /^(상품\\s*상세|상세\\s*정보|상품\\s*정보)$/.test(
                        (element.innerText || '').replace(/\\s+/g, '')
                    )
                );
                const detailStartY = detailHeadings.length
                    ? Math.min(...detailHeadings.map(element =>
                        element.getBoundingClientRect().top + window.scrollY
                    ))
                    : 500;
                const largeImages = Array.from(document.querySelectorAll('img, picture'))
                    .filter(element => {
                        const image = element.tagName === 'IMG'
                            ? element : element.querySelector('img');
                        if (!image) return false;
                        const rect = image.getBoundingClientRect();
                        const absoluteY = rect.top + window.scrollY;
                        const width = Math.max(rect.width, image.naturalWidth || 0);
                        const height = Math.max(rect.height, image.naturalHeight || 0);
                        if (absoluteY < detailStartY || width < 500 || height < 180) return false;
                        const productLink = image.closest('a[href*="/vp/products/"]');
                        if (productLink) return false;
                        for (let node = image; node && node !== document.body; node = node.parentElement) {
                            const identity = `${node.id || ''} ${node.className || ''}`;
                            if (excluded.test(identity)) return false;
                        }
                        return true;
                    });
                if (largeImages.length >= 2) {
                    let root = largeImages[0].parentElement;
                    const last = largeImages[largeImages.length - 1];
                    while (root && root !== document.body && !root.contains(last)) {
                        root = root.parentElement;
                    }
                    if (root && root !== document.body) {
                        const rect = root.getBoundingClientRect();
                        const height = Math.max(rect.height, root.scrollHeight);
                        if (rect.width >= 300 && height >= 300) {
                            candidates.push({
                                element: root,
                                priority: 0,
                                textLength: (root.innerText || '').trim().length,
                                imageCount: largeImages.length,
                                height
                            });
                        }
                    }
                }
            }
            candidates.sort((a, b) =>
                b.priority - a.priority ||
                (b.textLength + b.imageCount * 200) -
                    (a.textLength + a.imageCount * 200) ||
                b.height - a.height
            );
            let selected = candidates[0];
            if (!selected) return null;
            // 상세가 여러 형제 블록(상품설명/옵션목록/규격표)으로 나뉜
            // 페이지는 첫 블록 하나만 잡으면 뒤쪽 옵션 목록이 빠진다. 같은
            // 우선순위의 상세 블록을 모두 감싸는 가장 가까운 부모로 확장한다.
            const sameLevel = candidates.filter(
                item => item.priority === selected.priority
            );
            if (sameLevel.length > 1) {
                let combined = selected.element;
                for (const item of sameLevel.slice(1)) {
                    while (
                        combined && combined !== document.body
                        && !combined.contains(item.element)
                    ) {
                        combined = combined.parentElement;
                    }
                }
                if (combined && combined !== document.body) {
                    const combinedRect = combined.getBoundingClientRect();
                    const combinedHeight = Math.max(
                        combinedRect.height, combined.scrollHeight
                    );
                    if (combinedRect.width >= 300 && combinedHeight >= selected.height) {
                        selected = {
                            element: combined,
                            priority: selected.priority,
                            textLength: (combined.innerText || '').trim().length,
                            imageCount: combined.querySelectorAll('img, picture, table').length,
                            height: combinedHeight
                        };
                    }
                }
            }
            // '옵션 목록/옵션 선택'이 별도 형제 블록인 경우 그 제목부터
            // 아래 규격 문구까지 캡처 루트에 포함한다. 상단 구매옵션 UI의
            // 단순 '옵션' 문구는 대상에 넣지 않는다.
            const selectedTop = selected.element.getBoundingClientRect().top + window.scrollY;
            const optionMarker = Array.from(document.querySelectorAll(
                'h1, h2, h3, h4, strong, b, p, div, span'
            )).find(element => {
                const text = (element.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!/^(옵션\\s*(목록|선택|안내)|색상\\s*분류)/.test(text)) return false;
                if (text.length > 80) return false;
                const y = element.getBoundingClientRect().top + window.scrollY;
                return y >= selectedTop - 100;
            });
            if (optionMarker && !selected.element.contains(optionMarker)) {
                let combined = selected.element;
                while (
                    combined && combined !== document.body
                    && !combined.contains(optionMarker)
                ) {
                    combined = combined.parentElement;
                }
                if (combined && combined !== document.body) {
                    const rect = combined.getBoundingClientRect();
                    const height = Math.max(rect.height, combined.scrollHeight);
                    if (rect.width >= 300 && height <= 320000) {
                        selected = {
                            element: combined,
                            priority: selected.priority,
                            textLength: (combined.innerText || '').trim().length,
                            imageCount: combined.querySelectorAll('img, picture, table').length,
                            height
                        };
                    }
                }
            }
            const currentProduct = location.pathname.match(/\\/products\\/(\\d+)/)?.[1] || '';
            const hasForeignProduct = Array.from(
                selected.element.querySelectorAll('a[href*="/vp/products/"]')
            ).some(link => {
                try {
                    const match = new URL(link.href, location.href).pathname.match(
                        /\\/products\\/(\\d+)/
                    );
                    return match && (!currentProduct || match[1] !== currentProduct);
                } catch (_) {
                    return true;
                }
            });
            if (hasForeignProduct) return null;
            selected.element.setAttribute('data-codex-detail-capture', token);
            const rect = selected.element.getBoundingClientRect();
            return {
                x: Math.max(0, rect.left + window.scrollX),
                y: Math.max(0, rect.top + window.scrollY),
                width: rect.width,
                height: Math.max(rect.height, selected.element.scrollHeight)
            };
        }""",
        token,
    )
    if not region:
        return []

    detail = source_page.locator(f'[data-codex-detail-capture="{token}"]')
    if not detail.count():
        return []

    # 상세영역 안쪽을 끝까지 실제로 스크롤하여 지연 로딩 이미지와 HTML
    # 텍스트를 모두 렌더링시킨다. 리뷰·추천상품 등 바깥 영역은 캡처하지 않는다.
    page_height = int(region.get("height") or 0)
    page_y = int(region.get("y") or 0)
    for offset in range(0, min(page_height, 320_000), 700):
        source_page.evaluate(
            "position => window.scrollTo(0, position)", page_y + offset
        )
        source_page.wait_for_timeout(120)
    source_page.wait_for_timeout(700)

    # 지연 로딩으로 높이가 늘어날 수 있으므로 최종 위치와 크기를 다시 읽는다.
    region = detail.evaluate(
        """element => {
            const rect = element.getBoundingClientRect();
            return {
                x: Math.max(0, rect.left + window.scrollX),
                y: Math.max(0, rect.top + window.scrollY),
                width: rect.width,
                height: Math.max(rect.height, element.scrollHeight),
                documentWidth: document.documentElement.scrollWidth,
                documentHeight: document.documentElement.scrollHeight
            };
        }"""
    )
    x = max(0, int(region.get("x") or 0))
    y = max(0, int(region.get("y") or 0))
    width = min(
        max(1, int(region.get("width") or 0)),
        max(1, int(region.get("documentWidth") or 0) - x),
    )
    height = min(
        max(0, int(region.get("height") or 0)),
        max(0, int(region.get("documentHeight") or 0) - y),
        320_000,
    )
    if width < 300 or height < 150:
        return []

    # 상세 밖의 고정형 채팅/쿠키/광고 버튼이 각 조각에 반복되어 찍히지 않게
    # 숨긴다. 상세영역 내부 요소는 어떤 내용이든 그대로 둔다.
    source_page.evaluate(
        """token => {
            const root = document.querySelector(`[data-codex-detail-capture="${token}"]`);
            if (!root) return;
            for (const element of document.body.querySelectorAll('*')) {
                if (root.contains(element) || element.contains(root)) continue;
                const style = getComputedStyle(element);
                if (style.position !== 'fixed' && style.position !== 'sticky') continue;
                element.setAttribute('data-codex-capture-hidden', '1');
                element.style.setProperty('visibility', 'hidden', 'important');
            }
        }""",
        token,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("detail_page_*.png"):
        stale.unlink()
    paths: list[str] = []
    chunk_height = 3_500
    for index, offset in enumerate(range(0, height, chunk_height), start=1):
        destination = output_dir / f"detail_page_{index:03d}.png"
        capture_height = min(chunk_height, height - offset)
        try:
            source_page.screenshot(
                path=str(destination),
                clip={
                    "x": x,
                    "y": y + offset,
                    "width": width,
                    "height": capture_height,
                },
                animations="disabled",
                timeout=30_000,
            )
            if _valid_image_file(destination):
                paths.append(str(destination))
            elif destination.exists():
                destination.unlink()
        except Exception:
            if destination.exists() and not _valid_image_file(destination):
                destination.unlink()
            break
    return paths


def _download_source_details_once(
    context, job: dict, output_dir: Path
) -> tuple[list[str], str]:
    source_page = context.new_page()
    try:
        source_page.goto(job["source_url"], wait_until="domcontentloaded", timeout=60_000)
        # 쿠팡 상품 상세는 첫 화면이 열린 뒤 별도 요청으로 늦게 붙는 상품이
        # 있다. 고정 2초만 기다리지 않고 상세영역 또는 원본 이미지가 생길
        # 때까지 기다려 빈 페이지를 정상 상세로 오인하지 않는다.
        detail_ready_selector = (
            ".product-detail-content-inside, .product-detail-content, "
            "#productDetail, .product-detail, #productDescription, "
            "[class*='product-detail-content'], .product-description, "
            "[data-component*='product-detail'], "
            "img[src*='image.coupangcdn.com/image/vendor_inventory']"
        )
        ready_deadline = time.monotonic() + 15
        while time.monotonic() < ready_deadline:
            if source_page.locator(detail_ready_selector).count():
                break
            source_page.wait_for_timeout(500)
        # 상세영역이 접혀 있거나 스크롤 뒤에 지연 로딩되는 상품을 펼친다.
        expanders = source_page.locator("button, a").filter(
            has_text=re.compile(r"상품\s*(?:정보|상세).*더보기|상세.*펼쳐보기")
        )
        for index in range(expanders.count()):
            try:
                expander = expanders.nth(index)
                if expander.is_visible():
                    expander.click(force=True, timeout=3_000)
                    source_page.wait_for_timeout(500)
                    break
            except Exception:
                continue
        for ratio in (0.35, 0.6, 0.82, 1.0):
            source_page.evaluate(
                "ratio => window.scrollTo(0, document.body.scrollHeight * ratio)",
                ratio,
            )
            source_page.wait_for_timeout(450)

        body_text = source_page.locator("body").inner_text(timeout=15_000)
        block_reason = _source_page_block_reason(body_text)
        if block_reason:
            raise WingAutomationError(block_reason)
        # 접근 가능한 새 원본을 확인한 뒤에만 이전 수집물을 비운다. 과거의
        # 페이지 전체 URL 수집 결과(추천상품 포함)가 재시도에서 섞이지 않게 한다.
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "source_capture_manifest.json"
        if manifest_path.exists():
            manifest_path.unlink()
        for stale in output_dir.glob("detail_*"):
            if stale.is_file():
                stale.unlink()
        country = _extract_country(body_text)

        # 먼저 상세영역 전체를 렌더링된 모습 그대로 캡처한다. 기존처럼 img
        # 주소만 수집하면 HTML 텍스트·표·옵션 설명이 빠지기 때문이다.
        rendered_detail = _capture_rendered_detail_page(source_page, output_dir)
        if rendered_detail:
            return rendered_detail, country

        # 구형/특수 상품에서 상세 컨테이너를 찾지 못한 경우에만 기존의
        # 상세 이미지 주소 수집 방식을 안전한 대체 경로로 사용한다.
        detail_roots = (
            '[data-codex-detail-capture]',
            ".product-detail-content-inside",
            "#productDetail",
            ".product-detail-content",
            ".product-detail",
            "#productDescription",
            "[class*='product-detail-content']",
            ".product-description",
            ".product-detail-seemore-content",
            ".product-detail-seemore",
            "[data-component*='product-detail']",
            "[class*='productDetail']",
            "[id*='productDetail']",
        )
        urls: list[str] = []
        seen: set[str] = set()
        elements_by_url: dict[str, object] = {}
        # 상세 컨테이너마다 다른 상품 링크가 포함됐는지 먼저 검사한다.
        # 안전한 컨테이너 내부의 img와 JSON만 읽고 페이지 전체는 절대 훑지 않는다.
        for root_selector in detail_roots:
            roots = source_page.locator(root_selector)
            for root_index in range(min(roots.count(), 8)):
                root = roots.nth(root_index)
                try:
                    safe_root = root.evaluate(
                        """element => {
                            const current = location.pathname.match(/\\/products\\/(\\d+)/)?.[1] || '';
                            return !Array.from(
                                element.querySelectorAll('a[href*="/vp/products/"]')
                            ).some(link => {
                                try {
                                    const match = new URL(link.href, location.href)
                                        .pathname.match(/\\/products\\/(\\d+)/);
                                    return match && (!current || match[1] !== current);
                                } catch (_) {
                                    return true;
                                }
                            });
                        }"""
                    )
                    if not safe_root:
                        continue
                    images = root.locator("img")
                    for image_index in range(min(images.count(), 250)):
                        image = images.nth(image_index)
                        raw_values = [
                            image.get_attribute("src") or "",
                            image.get_attribute("data-src") or "",
                            image.get_attribute("data-lazy-src") or "",
                            image.get_attribute("data-origin") or "",
                        ]
                        srcset = image.get_attribute("srcset") or ""
                        raw_values.extend(
                            part.strip().split(" ", 1)[0]
                            for part in srcset.split(",")
                            if part.strip()
                        )
                        for raw in raw_values:
                            url = _original_coupang_image_url(raw)
                            if (
                                url.startswith("http")
                                and "coupangcdn.com" in url
                                and url not in seen
                            ):
                                seen.add(url)
                                urls.append(url)
                                elements_by_url[url] = image

                    # 일부 신형 상품은 상세 URL을 img가 아닌 상세 컨테이너
                    # 내부 JSON에만 보관한다.
                    html = (
                        root.inner_html()
                        .replace("\\/", "/")
                        .replace("\\u002F", "/")
                    )
                except Exception:
                    continue
                for raw in re.findall(
                    r"https?://[^\"'<>\s]+coupangcdn\.com/[^\"'<>\s]+",
                    html,
                    re.IGNORECASE,
                ):
                    url = _original_coupang_image_url(raw.rstrip("\\"))
                    if (
                        url not in seen
                        and any(
                            token in url
                            for token in ("vendor_inventory", "retail/images")
                        )
                    ):
                        seen.add(url)
                        urls.append(url)

        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        # 옵션 안내 이미지가 상세페이지 뒤쪽에 있는 상품도 누락하지 않는다.
        for index, url in enumerate(urls[:250], start=1):
            destination = output_dir / f"detail_{index:03d}{_safe_suffix(url)}"
            try:
                response = context.request.get(url, timeout=30_000)
                if response.ok:
                    destination.write_bytes(response.body())
                if _valid_image_file(destination):
                    paths.append(str(destination))
                    continue
                if destination.exists():
                    destination.unlink()
            except Exception:
                if destination.exists() and not _valid_image_file(destination):
                    destination.unlink()

            # CDN 요청만 막힌 경우에는 같은 이미지를 표시 중인 Chrome에서
            # 직접 캡처해 상세 이미지를 보존한다.
            image = elements_by_url.get(url)
            if image is not None:
                try:
                    if image.is_visible():
                        rendered = output_dir / f"detail_{index:03d}.png"
                        image.screenshot(path=str(rendered), timeout=10_000)
                        if _valid_image_file(rendered):
                            paths.append(str(rendered))
                except Exception:
                    continue

        if not paths:
            # URL 자체가 감춰진 마지막 경우에는 화면에 렌더링된 큰 이미지
            # 요소만 캡처한다. 아이콘·추천상품 썸네일은 크기로 제외한다.
            rendered_images = source_page.locator("img:visible")
            for index in range(min(rendered_images.count(), 500)):
                if len(paths) >= 250:
                    break
                image = rendered_images.nth(index)
                try:
                    belongs_to_detail = image.evaluate(
                        """element => {
                            const excluded = /review|recommend|related|similar|carousel|thumbnail|search|ranking/i;
                            if (element.closest('a[href*="/vp/products/"]')) return false;
                            for (let node = element; node && node !== document.body; node = node.parentElement) {
                                const identity = `${node.id || ''} ${node.className || ''}`;
                                if (excluded.test(identity)) return false;
                                if (
                                    node.hasAttribute('data-codex-detail-capture')
                                    || /product.?detail|product.?description/i.test(identity)
                                ) return true;
                            }
                            return false;
                        }"""
                    )
                    if not belongs_to_detail:
                        continue
                    bounds = image.bounding_box() or {}
                    width = int(bounds.get("width") or 0)
                    height = int(bounds.get("height") or 0)
                    if width < 300 or height < 180:
                        continue
                    destination = output_dir / f"detail_rendered_{len(paths) + 1:03d}.png"
                    image.screenshot(path=str(destination), timeout=10_000)
                    if _valid_image_file(destination):
                        paths.append(str(destination))
                except Exception:
                    continue
        if not paths:
            raise WingAutomationError(
                "상품 상세영역과 원본 상세이미지를 페이지에서 찾지 못했습니다."
            )
        return paths, country
    except Exception as exc:
        # 실패 화면을 남겨 다음 쿠팡/WING 변경 때 원인을 즉시 확인할 수 있게
        # 한다. 진단 저장 자체의 실패는 원래 자동화 흐름을 방해하지 않는다.
        try:
            attempt = re.sub(
                r"[^0-9A-Za-z_-]+",
                "_",
                str(job.get("_source_attempt") or "attempt"),
            )
            diagnostics = output_dir.parent / "source_diagnostics"
            diagnostics.mkdir(parents=True, exist_ok=True)
            (diagnostics / f"{attempt}.html").write_text(
                source_page.content(), encoding="utf-8"
            )
            diagnostic_body = source_page.locator("body").inner_text(timeout=3_000)
            detected_block = _source_page_block_reason(diagnostic_body)
            if detected_block:
                job["_source_blocked"] = detected_block
            (diagnostics / f"{attempt}.txt").write_text(
                "\n".join(
                    (
                        f"URL: {job.get('source_url', '')}",
                        f"오류: {type(exc).__name__}: {exc}",
                        f"화면: {' '.join(diagnostic_body.split())[:4000]}",
                    )
                ),
                encoding="utf-8",
            )
            source_page.screenshot(
                path=str(diagnostics / f"{attempt}.png"),
                full_page=False,
                animations="disabled",
                timeout=10_000,
            )
        except Exception:
            pass
        return [], ""
    finally:
        source_page.close()


def _download_source_details(context, job: dict, output_dir: Path) -> tuple[list[str], str]:
    """원상품 상세를 URL 변형과 새 탭으로 최대 세 번 안전하게 수집한다."""

    country = ""
    candidates = _source_detail_url_candidates(str(job.get("source_url") or ""))
    attempt_urls = list(candidates[:3])
    while attempt_urls and len(attempt_urls) < 3:
        attempt_urls.append(attempt_urls[-1])
    for index, candidate in enumerate(attempt_urls, start=1):
        if index == 1:
            _wait_for_source_access_slot(output_dir)
        attempt_job = dict(job)
        attempt_job["source_url"] = candidate
        attempt_job["_source_attempt"] = f"attempt_{index}"
        paths, detected_country = _download_source_details_once(
            context, attempt_job, output_dir
        )
        country = country or detected_country
        if paths:
            _record_source_access_success(output_dir)
            (output_dir / "source_capture_manifest.json").write_text(
                json.dumps(
                    {
                        "capture_version": 2,
                        "detail_count": len(paths),
                        "includes_full_detail_region": True,
                        "excludes_related_products": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return paths, country
        block_reason = str(attempt_job.get("_source_blocked") or "")
        if block_reason:
            _record_source_access_block(output_dir, block_reason)
            # 차단 중 URL만 바꿔 연속 재접속하면 제한 시간이 늘어난다. 한 번
            # 충분히 쉰 뒤 새 탭으로 재시도하고, 두 번째도 차단이면 다음
            # 상품까지 공용 휴식 상태를 유지한다.
            if index >= 2:
                break
            _wait_for_source_access_slot(output_dir)
        if index < len(attempt_urls):
            # 같은 탭을 새로고침하면 차단/빈 DOM이 유지될 수 있어 매회 새 탭을
            # 사용하고, 요청 간격도 조금 둔다.
            time.sleep(1.5 * index)
    return [], country


def _existing_paths(values: Iterable[str]) -> list[str]:
    return [str(Path(value)) for value in values if Path(value).is_file()]


def _apply_configured_watermark(
    paths: list[str], *, output_dir: Path, watermark_settings: dict
) -> list[str]:
    if not paths or not watermark_settings.get("enabled"):
        return paths
    try:
        return apply_watermark_to_images(
            source_paths=paths,
            output_dir=output_dir,
            image_path=str(watermark_settings.get("image_path") or ""),
            text=str(watermark_settings.get("text") or ""),
            position=str(watermark_settings.get("position") or "bottom_right"),
            opacity_percent=int(watermark_settings.get("opacity_percent") or 25),
            size_percent=int(watermark_settings.get("size_percent") or 15),
            angle_degrees=int(watermark_settings.get("angle_degrees") or 0),
        )
    except Exception:
        return paths


def _detail_upload_assets(
    asset_paths: list[str],
    *,
    output_dir: Path,
    maximum_files: int = MAX_DETAIL_UPLOAD_FILES,
    panel_width: int = DETAIL_PANEL_WIDTH,
    panel_max_height: int = DETAIL_PANEL_MAX_HEIGHT,
) -> list[str]:
    """각 원본 상세이미지의 경계를 보존한 WING 업로드 파일을 만든다.

    좁은 이미지는 780px로 정규화하고 한 이미지가 높이 제한을 넘을 때만
    여러 조각으로 나눈다. 서로 다른 원본 이미지는 절대 한 파일로 합치지
    않아 WING 에디터에서 각 이미지를 따로 편집·삭제할 수 있게 한다.
    ``maximum_files``는 한 번의 업로드 세션 크기이며 전체 결과 수 제한이 아니다.
    """

    assets = [Path(value) for value in _existing_paths(asset_paths)]
    if not assets:
        return []

    results: list[str] = []
    needs_output = False
    for asset in assets:
        with Image.open(asset) as image:
            normalized_height = max(
                1, round(image.height * panel_width / image.width)
            )
            if image.width != panel_width or normalized_height > panel_max_height:
                needs_output = True
                break
    if not needs_output:
        return [str(asset) for asset in assets]

    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("detail_section_*.jpg"):
        stale.unlink()

    for source_index, asset in enumerate(assets, start=1):
        with Image.open(asset) as opened:
            target_height = max(
                1, round(opened.height * panel_width / opened.width)
            )
            if opened.width == panel_width and target_height <= panel_max_height:
                results.append(str(asset))
                continue

            image = opened.convert("RGBA")
            if image.size != (panel_width, target_height):
                image = image.resize(
                    (panel_width, target_height), Image.Resampling.LANCZOS
                )
            flattened = Image.new("RGB", image.size, "white")
            flattened.paste(image, mask=image.getchannel("A"))
            image.close()

            part_index = 1
            for source_y in range(0, flattened.height, panel_max_height):
                part = flattened.crop(
                    (
                        0,
                        source_y,
                        panel_width,
                        min(source_y + panel_max_height, flattened.height),
                    )
                )
                destination = output_dir / (
                    f"detail_section_{source_index:03d}_{part_index:02d}.jpg"
                )
                part.save(destination, format="JPEG", quality=94, optimize=True)
                part.close()
                results.append(str(destination))
                part_index += 1
            flattened.close()
    return results


def _detail_urls_from_wing_state(html: str) -> list[str]:
    """WING 폼 상태 JSON에 저장된 상세이미지 CDN 주소를 순서대로 읽는다."""

    source = html or ""
    paths = re.findall(
        r'"imageType"\s*:\s*"DETAIL"\s*,\s*"cdnPath"\s*:\s*"(vendor_inventory/[^"\\]+)"',
        source,
    )
    # 신형 WING의 이미지 업로드 등록은 images 배열이 아니라 contents 배열에
    # 상세이미지 경로를 보관한다.
    paths.extend(
        re.findall(
            r'"content"\s*:\s*"(vendor_inventory/[^"\\]+)"\s*,\s*"detailType"\s*:\s*"IMAGE"',
            source,
        )
    )
    urls: list[str] = []
    for path in paths:
        url = f"https://image.coupangcdn.com/image/{path}"
        if url not in urls:
            urls.append(url)
    return urls


def _current_detail_upload_input(upload_dialog):
    """업로드 과정에서 교체될 수 있는 최신 파일 입력칸을 반환한다."""

    file_inputs = upload_dialog.locator('input[type="file"]')
    if not file_inputs.count():
        raise WingAutomationError("상세 이미지 파일 입력칸을 찾지 못했습니다.")
    return file_inputs.last


def _select_detail_upload_files(
    page,
    upload_dialog,
    upload,
    batch: list[str],
    *,
    force_individual: bool = False,
):
    """WING 업로더의 현재 입력칸에 파일을 전달하고 최신 입력칸을 돌려준다.

    신형 WING 업로더는 파일을 고른 직후 ``input[type=file]``을 새 요소로
    교체한다. 같은 locator를 여러 장에 재사용하면 화면에는 0/N장으로 남을
    수 있으므로, 한 장씩 보낼 때마다 입력칸을 다시 찾는다.
    """

    if (
        not force_individual
        and (upload.get_attribute("multiple") is not None or len(batch) <= 1)
    ):
        upload.set_input_files(batch)
        return _current_detail_upload_input(upload_dialog)

    previews = upload_dialog.locator(
        '[filename], .dz-preview, img[src*="/image/vendor_inventory/"]'
    )
    known_count = previews.count()
    for asset in batch:
        upload = _current_detail_upload_input(upload_dialog)
        upload.set_input_files(asset)
        filename = Path(asset).name
        # 파일이 전혀 큐에 들어가지 않는 경우 1장마다 60초씩 기다리지
        # 않는다. 아래 묶음 재시도 단계에서 새 입력칸으로 다시 보낸다.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            named = upload_dialog.locator(f'[filename="{filename}"]')
            current_count = previews.count()
            if named.count() or current_count > known_count:
                known_count = max(known_count + 1, current_count)
                break
            page.wait_for_timeout(250)
        # 파일선택 input은 마지막 1장만 보유해도 업로더 큐에는 앞선 파일이
        # 이미 들어가 있다. 다음 change 이벤트 전에 짧게 여유를 둔다.
        page.wait_for_timeout(250)
    return _current_detail_upload_input(upload_dialog)


def _wait_for_detail_upload(page, upload_dialog, upload, batch: list[str]) -> tuple[list[str], bool]:
    """상세이미지 묶음의 CDN 업로드 완료 또는 저장 가능 상태를 기다린다."""

    expected_filenames = {Path(asset).name for asset in batch}
    upload_deadline = time.monotonic() + 180
    no_progress_deadline = time.monotonic() + 25
    best_progress = 0
    while time.monotonic() < upload_deadline:
        completed_by_name: dict[str, str] = {}
        completed = upload_dialog.locator('[finalizedimageurl^="vendor_inventory/"]')
        for index in range(completed.count()):
            item = completed.nth(index)
            filename = Path(item.get_attribute("filename") or "").name
            cdn_path = item.get_attribute("finalizedimageurl") or ""
            if filename in expected_filenames and cdn_path:
                completed_by_name[filename] = (
                    f"https://image.coupangcdn.com/image/{cdn_path}"
                )
        if len(completed_by_name) == len(expected_filenames):
            return (
                [completed_by_name[Path(asset).name] for asset in batch],
                False,
            )

        queued = upload_dialog.locator(
            '[filename], .dz-preview, img[src*="/image/vendor_inventory/"]'
        )
        queued_count = queued.count()
        progress = max(len(completed_by_name), queued_count)
        if progress > best_progress:
            best_progress = progress
            no_progress_deadline = time.monotonic() + 25

        modal_urls: list[str] = []
        modal_images = upload_dialog.locator('img[src*="/image/vendor_inventory/"]')
        for index in range(modal_images.count()):
            url = modal_images.nth(index).get_attribute("src") or ""
            if url.startswith("//"):
                url = "https:" + url
            if url and url not in modal_urls:
                modal_urls.append(url)
        if len(modal_urls) >= len(batch):
            return modal_urls[-len(batch) :], False

        modal_save_buttons = upload_dialog.locator("button").filter(
            has_text=re.compile(r"^\s*저장\s*$")
        )
        ready_saves = [
            modal_save_buttons.nth(index)
            for index in range(modal_save_buttons.count())
            if modal_save_buttons.nth(index).is_visible()
            and not modal_save_buttons.nth(index).is_disabled()
        ]
        try:
            upload = _current_detail_upload_input(upload_dialog)
            selected_file_count = upload.evaluate("element => element.files.length")
        except Exception:
            selected_file_count = 0
        if ready_saves and (
            selected_file_count == len(batch) or queued_count >= len(batch)
        ):
            page.wait_for_timeout(1_500)
            if not ready_saves[-1].is_disabled():
                return [], True
        if best_progress == 0 and time.monotonic() >= no_progress_deadline:
            # 파일 선택 이벤트 자체가 반영되지 않았다. 3분 전체 제한을
            # 기다리지 말고 최신 입력칸을 이용한 개별 재전송으로 넘어간다.
            return [], False
        page.wait_for_timeout(500)
    return [], False


def _upload_detail_as_editor(page, asset_paths: list[str]) -> int:
    assets = _existing_paths(asset_paths)
    if not assets:
        return 0

    def trace(message: str) -> None:
        print(f"[WING detail] {message}", flush=True)

    trace(f"start: {len(assets)} file(s)")

    def radio_is_selected(radio, label) -> bool:
        # Current WING create/modify screens mark the active Vue toggle with
        # `type:primary`.  Trust that visible state before the hidden radio,
        # whose checked property can remain stale after changing entry modes.
        uicfg = label.get_attribute("data-sc-common-uicfg") or ""
        if uicfg:
            return "type:primary" in uicfg
        if radio.is_checked():
            return True
        return bool(
            label.evaluate(
                """element => {
                    const style = getComputedStyle(element);
                    return style.color === 'rgb(52, 106, 255)'
                        || style.backgroundColor === 'rgb(52, 106, 255)';
                }"""
            )
        )

    def select_radio(radio_id: str) -> None:
        radio = page.locator(f"#{radio_id}")
        label = page.locator(f'label[for="{radio_id}"]')
        if not radio.count() or not label.count():
            raise WingAutomationError("상세설명 등록 방식 선택항목을 찾지 못했습니다.")
        if radio_is_selected(radio, label):
            trace(f"{radio_id} already selected")
            return
        label.click()
        # WING renders the confirmation before its internal mode-change
        # handler is fully ready.  Clicking Confirm too quickly only closes
        # the dialog and leaves the previous mode selected.
        page.wait_for_timeout(1_000)
        confirmation = page.get_by_role("button", name="확인", exact=True)
        visible_confirmation = [
            confirmation.nth(index)
            for index in range(confirmation.count())
            if confirmation.nth(index).is_visible()
        ]
        if not visible_confirmation:
            page.wait_for_timeout(700)
            visible_confirmation = [
                confirmation.nth(index)
                for index in range(confirmation.count())
                if confirmation.nth(index).is_visible()
            ]
        trace(f"{radio_id} confirmation(s): {len(visible_confirmation)}")
        if visible_confirmation:
            visible_confirmation[-1].click()
            page.wait_for_timeout(300)
            trace(f"{radio_id} confirmation clicked")
            selection_deadline = time.monotonic() + 3
            while (
                not radio_is_selected(radio, label)
                and time.monotonic() < selection_deadline
            ):
                page.wait_for_timeout(150)
            # Some WING builds leave the toggle marker stale even though the
            # confirmation has switched the rendered panel.  The caller
            # verifies the panel by its action button, which is more reliable.
            if not radio_is_selected(radio, label):
                trace(f"{radio_id} marker stayed stale after confirmation")
            return
        if not radio_is_selected(radio, label):
            label.click(force=True)
            page.wait_for_timeout(300)
        trace(
            f"{radio_id} selected={radio_is_selected(radio, label)} "
            f"uicfg={label.get_attribute('data-sc-common-uicfg') or ''}"
        )
        if not radio_is_selected(radio, label):
            raise WingAutomationError("상세설명 등록 방식을 전환하지 못했습니다.")

    select_radio("tab-content-level-0")
    select_radio("tab-content-0")
    uploaded_urls: list[str] = []
    # WING 이미지 업로더는 한 번에 최대 10장만 받는다. 원본끼리 합치지 않고
    # 10장씩 별도 업로드 세션을 열어 CDN 주소를 모은 뒤 에디터에 각각 넣는다.
    for batch_start in range(0, len(assets), MAX_DETAIL_UPLOAD_FILES):
        batch = assets[batch_start : batch_start + MAX_DETAIL_UPLOAD_FILES]
        batch_number = batch_start // MAX_DETAIL_UPLOAD_FILES + 1
        upload_actions = page.get_by_text(
            re.compile(r"^\s*(?:수정하기|이미지\s*등록)\s*$")
        )
        write = None
        action_deadline = time.monotonic() + 8
        while time.monotonic() < action_deadline:
            try:
                write = _last_visible(upload_actions)
                break
            except WingAutomationError:
                page.wait_for_timeout(200)
        if write is None:
            raise WingAutomationError(
                "상세설명이 이미지 업로드 화면으로 전환되지 않았습니다."
            )
        write.click()
        upload_dialog = page.locator(".modal:visible").last
        full_delete = upload_dialog.get_by_text("전체 삭제", exact=True)
        if (
            full_delete.count()
            and full_delete.last.is_visible()
            and not full_delete.last.is_disabled()
        ):
            # 각 세션은 최대 10장만 잠시 보관하고, 최종 에디터 본문에는 모든
            # 세션의 CDN 주소를 다시 넣는다.
            full_delete.last.click()
            page.wait_for_timeout(350)
            _dismiss_visible_wing_confirmation(page, wait_ms=1_500)
            cleanup_deadline = time.monotonic() + 6
            while time.monotonic() < cleanup_deadline:
                old_previews = upload_dialog.locator(
                    '[finalizedimageurl^="vendor_inventory/"]:visible, '
                    '.dz-preview.dz-success:visible, .dz-preview.dz-complete:visible'
                )
                if not old_previews.count():
                    break
                page.wait_for_timeout(250)
            if old_previews.count():
                raise WingAutomationError(
                    "이전 상품의 상세 이미지가 업로드 창에서 지워지지 않았습니다."
                )
        file_inputs = upload_dialog.locator('input[type="file"]')
        try:
            file_inputs.last.wait_for(state="attached", timeout=10_000)
        except Exception:
            raise WingAutomationError("상세 이미지 파일 입력칸을 찾지 못했습니다.")
        upload = file_inputs.last
        try:
            upload.set_input_files([])
        except Exception:
            pass
        upload = _select_detail_upload_files(
            page, upload_dialog, upload, batch
        )
        trace(f"upload session {batch_number}: {len(batch)} file(s) selected")

        batch_urls, modal_ready_without_urls = _wait_for_detail_upload(
            page, upload_dialog, upload, batch
        )
        if len(batch_urls) != len(batch) and not modal_ready_without_urls:
            # WING 업로더가 파일을 받았지만 0/N에서 멈추는 간헐 오류는 같은
            # 창에서 파일 선택을 초기화하고 한 번 더 보내면 대부분 복구된다.
            # 재전송 전 대기해 이전 업로드 요청과 겹치지 않게 한다.
            trace(f"upload session {batch_number}: stalled; retrying once")
            try:
                upload = _current_detail_upload_input(upload_dialog)
                upload.set_input_files([])
            except Exception:
                pass
            page.wait_for_timeout(1_500)
            # 묶음 선택을 지원한다고 표시하는 신형 입력칸도 간헐적으로
            # change 이벤트를 무시한다. 재시도는 최신 입력칸을 매번 다시
            # 잡아 한 장씩 보내 WING 큐가 각 파일을 확실히 받게 한다.
            upload = _select_detail_upload_files(
                page,
                upload_dialog,
                upload,
                batch,
                force_individual=True,
            )
            batch_urls, modal_ready_without_urls = _wait_for_detail_upload(
                page, upload_dialog, upload, batch
            )
        if len(batch_urls) != len(batch) and not modal_ready_without_urls:
            raise WingAutomationError(
                f"상세 이미지 {batch_number}번째 묶음 업로드가 완료되지 않았습니다. "
                f"재전송 후에도 {len(batch_urls)}/{len(batch)}장입니다."
            )

        save = _last_visible(
            upload_dialog.locator("button").filter(
                has_text=re.compile(r"^\s*저장\s*$")
            )
        )
        deadline = time.monotonic() + 120
        while save.is_disabled() and time.monotonic() < deadline:
            page.wait_for_timeout(500)
        if save.is_disabled():
            raise WingAutomationError("상세 이미지 업로드가 2분 안에 끝나지 않았습니다.")
        save.click()
        page.wait_for_timeout(800)
        confirmation = page.locator("button.alert-confirm:visible")
        if confirmation.count():
            confirmation.last.click()
            page.wait_for_timeout(500)

        if len(batch_urls) < len(batch):
            discovered: list[str] = []
            uploaded_images = page.locator(
                'img[src*="/image/vendor_inventory/"]'
            )
            for index in range(uploaded_images.count()):
                url = uploaded_images.nth(index).get_attribute("src") or ""
                if url.startswith("//"):
                    url = "https:" + url
                if url and url not in discovered:
                    discovered.append(url)
            for url in _detail_urls_from_wing_state(page.content()):
                if url not in discovered:
                    discovered.append(url)
            new_urls = [url for url in discovered if url not in uploaded_urls]
            batch_urls = new_urls[-len(batch) :]
        if len(batch_urls) != len(batch):
            raise WingAutomationError(
                f"상세 이미지 {batch_number}번째 묶음의 WING 주소를 "
                f"확인하지 못했습니다. ({len(batch_urls)}/{len(batch)}장)"
            )
        uploaded_urls.extend(batch_urls)
        trace(f"upload session {batch_number} saved")

    # 기존 이미지 업로드 내용을 '에디터 작성' 방식으로 전환한다. WING의
    # 숨은 radio는 일반 check()를 되돌리는 경우가 있어 label 이벤트를 직접
    # 보낸 뒤, 변환 확인 알림을 승인하고 한 번 더 선택한다.
    select_radio("tab-content-1")
    trace("editor mode selected")

    html = "\n".join(
        '<p style="margin:0;text-align:center">'
        f'<img src="{url}" width="780" '
        'style="display:block;width:780px;max-width:100%;height:auto;margin:0 auto">'
        '</p>'
        for url in uploaded_urls
    )

    # 카탈로그 매칭 상품은 '기본 등록'이어도 옵션별 작성 행이 남을 수 있다.
    # WING은 저장 직후에도 버튼 문구를 '작성하기'로 유지하고, 완료 문구는
    # 비동기로 늦게 갱신한다. 버튼 개수를 기준으로 돌면 이미 저장한 행을
    # 다시 열 수 있으므로 실제 '작성된 내용이 없습니다' 행만 처리한다.
    def visible_empty_descriptions():
        empty = page.get_by_text("작성된 내용이 없습니다.", exact=True)
        return [
            empty.nth(index)
            for index in range(empty.count())
            if empty.nth(index).is_visible()
        ]

    saved_option_count = 0
    stalled_saves = 0
    editor_attempts = 0
    while True:
        empty_descriptions = visible_empty_descriptions()
        if not empty_descriptions:
            break
        if editor_attempts >= 60:
            raise WingAutomationError(
                f"상세설명 저장을 반복했지만 빈 옵션 {len(empty_descriptions)}개가 남았습니다."
            )
        before_empty_count = len(empty_descriptions)
        empty_panel = empty_descriptions[0].locator("xpath=..")
        write = _last_visible(empty_panel.get_by_text("작성하기", exact=True))
        write.click()
        editor_attempts += 1
        trace(
            f"editor attempt {editor_attempts} opened "
            f"(empty={before_empty_count})"
        )
        page.wait_for_timeout(500)
        editor_iframe = page.locator("iframe:visible").last
        editor_id = (editor_iframe.get_attribute("id") or "").removesuffix("_ifr")
        if not editor_id:
            raise WingAutomationError("WING 에디터 본문 입력칸을 찾지 못했습니다.")
        saved_html = page.evaluate(
            """({ editorId, html }) => {
                const editor = window.tinymce && window.tinymce.get(editorId);
                if (!editor) return '';
                editor.setContent(html);
                // 신형 WING의 Vue/TinyMCE 연결부는 change만으로는 편집 내용을
                // 폼 모델에 반영하지 않는다. input 이벤트와 원본 textarea의
                // DOM 이벤트까지 전달해야 저장 뒤 '작성된 내용이 있습니다'로
                // 확정된다.
                editor.fire('input');
                editor.fire('change');
                editor.save();
                const textarea = document.getElementById(editorId);
                if (textarea) {
                    textarea.value = html;
                    textarea.dispatchEvent(new Event('input', { bubbles: true }));
                    textarea.dispatchEvent(new Event('change', { bubbles: true }));
                }
                return editor.getContent();
            }""",
            {"editorId": editor_id, "html": html},
        )
        if str(saved_html).count("<img") != len(uploaded_urls):
            raise WingAutomationError("WING 에디터에 상세 이미지가 모두 입력되지 않았습니다.")
        trace(f"editor attempt {editor_attempts} content set")
        editor_dialog = page.locator(".modal:visible").last
        editor_save = _last_visible(
            editor_dialog.locator("button").filter(
                has_text=re.compile(r"^\s*저장\s*$")
            )
        )
        editor_save.click()
        trace(f"editor attempt {editor_attempts} save clicked")
        try:
            editor_dialog.wait_for(state="hidden", timeout=5_000)
        except Exception:
            pass
        _dismiss_visible_wing_confirmation(page, wait_ms=1_500)

        # 저장 응답이 느린 상품은 완료 표시가 5초 가까이 늦게 바뀐다.
        save_deadline = time.monotonic() + 7
        after_empty_count = before_empty_count
        while time.monotonic() < save_deadline:
            after_empty_count = len(visible_empty_descriptions())
            if after_empty_count < before_empty_count:
                break
            page.wait_for_timeout(250)

        if after_empty_count < before_empty_count:
            saved_option_count += before_empty_count - after_empty_count
            stalled_saves = 0
            trace(
                f"editor attempt {editor_attempts} confirmed "
                f"(remaining={after_empty_count})"
            )
        else:
            stalled_saves += 1
            trace(
                f"editor attempt {editor_attempts} not confirmed; retrying "
                f"(remaining={after_empty_count})"
            )
            if stalled_saves >= 3:
                raise WingAutomationError(
                    f"상세설명을 3회 저장했지만 빈 옵션 {after_empty_count}개의 "
                    "완료 표시가 확인되지 않았습니다."
                )

    trace("complete")
    return len(assets)


def _configure_images_and_details(
    page,
    context,
    job: dict,
    *,
    fixed_top_paths: list[str],
    fixed_bottom_paths: list[str],
    watermark_settings: dict,
    work_root: Path,
) -> tuple[int, str, list[str]]:
    country = str(job.get("country_of_origin") or "").strip()
    translation_approved = bool(job.get("translation_review_approved"))
    detail_paths = _existing_paths(
        job.get("translated_detail_image_paths") or []
    ) if translation_approved else []
    prepared_source_paths = _existing_paths(
        job.get("source_detail_image_paths") or []
    )
    notes: list[str] = []
    if not detail_paths:
        downloaded, detected_country = _download_source_details(
            context, job, work_root / "source"
        )
        if not downloaded:
            # 사용자가 앞서 준비해 둔 상세이미지는 새 전체 페이지 캡처가
            # 불가능한 경우의 대체 자료로만 사용한다.
            downloaded = prepared_source_paths
        if not downloaded:
            # 원상품 페이지가 일시적으로 이미지를 내주지 않는 재시도에서는
            # 새 수집 방식으로 검증된 결과만 재사용한다. 구버전 결과에는
            # 추천상품 이미지가 섞이거나 뒤쪽 옵션 목록이 잘린 사례가 있다.
            source_root = work_root / "source"
            manifest_path = source_root / "source_capture_manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                manifest = {}
            downloaded = (
                [
                    str(path)
                    for path in sorted(source_root.glob("detail_*"))
                    if path.is_file()
                ]
                if int(manifest.get("capture_version") or 0) >= 2
                else []
            )
            if downloaded:
                notes.append(f"기존 상세 이미지 {len(downloaded)}장 재사용")
        detail_paths, removed_seller_pages = filter_known_seller_pages(downloaded)
        country = country or detected_country
        if removed_seller_pages:
            notes.append(
                f"다른 판매자 고정페이지 {len(removed_seller_pages)}장 자동 제거"
            )
        if detail_paths:
            detail_paths = _apply_configured_watermark(
                detail_paths,
                output_dir=work_root / "watermarked_detail",
                watermark_settings=watermark_settings,
            )
        else:
            raise WingAutomationError(
                "원상품 상세 이미지를 가져오지 못해 불완전한 상품등록을 중단했습니다."
            )

    assets = (
        _existing_paths(fixed_top_paths)
        + detail_paths
        + _existing_paths(fixed_bottom_paths)
    )
    upload_assets = _detail_upload_assets(
        assets,
        output_dir=work_root / "editor_upload",
    )
    uploaded = _upload_detail_as_editor(page, upload_assets)
    if uploaded:
        if len(upload_assets) < len(assets):
            notes.append(
                f"상세 이미지 {len(assets)}장을 업로드 패널 {uploaded}장으로 합쳐 적용"
            )
        else:
            notes.append(f"에디터 상세 이미지 {uploaded}장 적용")
    else:
        notes.append("에디터 상세 이미지 없음")
    return uploaded, country, notes


def prepare_job_in_wing(
    job: dict,
    *,
    profile_dir: Path,
    work_root: Path,
    fixed_top_paths: list[str],
    fixed_bottom_paths: list[str],
    watermark_settings: dict,
    progress: ProgressCallback | None = None,
    port: int = DEFAULT_DEBUG_PORT,
) -> dict:
    """카탈로그 매칭부터 필수값 입력까지 수행하고 최종 버튼 앞에서 멈춘다."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise WingAutomationError(
            "자동입력 구성요소가 설치되지 않았습니다. 설치.bat을 다시 실행하세요."
        ) from exc

    launch_controlled_chrome(profile_dir=profile_dir, port=port)
    with sync_playwright() as playwright:
        browser = _connect_controlled_chrome(
            playwright,
            profile_dir=profile_dir,
            port=port,
        )
        context, page = _wing_page(browser)
        _notify(progress, "opening_wing", "WING 상품등록 화면을 확인하는 중입니다.")
        page = _wait_for_registration(context, page, progress)
        _reset_registration_form(page)

        matched: list[dict] = []
        option_notes: list[str] = []
        deferred_option_matching = False
        for selection_attempt in range(2):
            try:
                _notify(
                    progress,
                    "catalog_search",
                    "쿠팡 카탈로그와 옵션을 검색하는 중입니다."
                    if selection_attempt == 0
                    else "옵션표 반영이 늦어 카탈로그 선택을 자동으로 다시 시도합니다.",
                )
                matched = _select_catalog_options(
                    page, str(job.get("search_value") or "")
                )
                _notify(
                    progress,
                    "catalog_selected",
                    f"카탈로그 옵션 {len(matched)}개를 선택했습니다. "
                    f"최저가는 {min(option['winner_price'] for option in matched):,}원입니다.",
                )
                try:
                    option_notes = _complete_missing_required_options(
                        page,
                        matched,
                        product_id=str(job.get("product_id") or ""),
                    )
                except _DeferredOptionMatching:
                    deferred_option_matching = True
                    option_notes = ["필수정보 입력 후 옵션 매칭 재시도 예정"]
                if option_notes:
                    _notify(
                        progress,
                        "required_options_completed",
                        ", ".join(option_notes),
                    )
                _notify(
                    progress,
                    "filling_options",
                    "매칭 판매가와 재고 999개를 입력하는 중입니다.",
                )
                _fill_option_prices_and_stock(page, matched)
                _notify(
                    progress,
                    "disabling_auto_pricing",
                    "모든 옵션의 자동가격조정을 비활성화하는 중입니다.",
                )
                _disable_auto_price_adjustment(page)
                option_notes.append("자동가격조정 비활성 상태 확인")
                break
            except WingAutomationError as exc:
                option_render_error = any(
                    token in str(exc)
                    for token in (
                        "옵션 판매가 입력표",
                        "옵션 선택 결과",
                        "판매가 입력 행",
                    )
                )
                if selection_attempt or not option_render_error:
                    raise
                _reset_registration_form(page)

        minimum_price = min(option["winner_price"] for option in matched)

        _notify(
            progress,
            "uploading_product_image",
            "옵션별 대표이미지를 준비해 WING에 넣는 중입니다.",
        )
        main_image = _download_source_main_image(
            context,
            job,
            work_root / "product_image",
        )
        watermarked_main = _apply_configured_watermark(
            [main_image] if main_image else [],
            output_dir=work_root / "watermarked_product_image",
            watermark_settings=watermark_settings,
        )
        fallback_main = watermarked_main[0] if watermarked_main else main_image
        ready_watermarked_options = _existing_paths(
            job.get("watermarked_option_image_paths") or []
        )
        if ready_watermarked_options:
            option_image_paths = _align_option_image_paths(
                ready_watermarked_options,
                option_count=len(matched),
                fallback_main=fallback_main,
            )
        else:
            ready_source_options = _existing_paths(
                job.get("source_option_image_paths") or []
            )
            if ready_source_options:
                option_image_paths = _align_option_image_paths(
                    ready_source_options,
                    option_count=len(matched),
                    fallback_main=main_image,
                )
            else:
                option_image_paths = _download_option_images(
                    context,
                    matched,
                    fallback_main=main_image,
                    output_dir=work_root / "option_images",
                )
            option_image_paths = _apply_configured_watermark(
                option_image_paths,
                output_dir=work_root / "watermarked_option_images",
                watermark_settings=watermark_settings,
            )
            option_image_paths = _align_option_image_paths(
                option_image_paths,
                option_count=len(matched),
                fallback_main=fallback_main,
            )
        if len(matched) == 1:
            # WING은 옵션이 하나뿐인 상품에서 옵션별 이미지표(#imageTable)를
            # 렌더링하지 않는 화면이 있다. 이 경우 같은 옵션 이미지를 기본
            # 대표이미지로 넣는 것이 실제 WING 동작과 동일하다.
            _upload_product_main_image(
                page,
                option_image_paths[0] if option_image_paths else fallback_main,
            )
            option_notes.append("단일 옵션 기본 대표이미지 적용")
        else:
            _upload_option_product_images(page, matched, option_image_paths)
            option_notes.append(f"옵션별 대표이미지 {len(option_image_paths)}장 적용")

        _notify(progress, "filling_policy", "배송·출고·반품·고시·KC 값을 입력하는 중입니다.")
        # 새 WING은 배송방법을 '구매대행'으로 먼저 바꿔야 KC 구매대행 예외와
        # 배송비/출고일 입력란을 렌더링한다.
        _set_shipping(page, str(job.get("shipping_fee_type") or ""))
        _ensure_purchase_agent_certification(page)
        _set_product_notice(page)
        address_notes = _select_saved_addresses_if_needed(page)
        return_fee = _set_return_fee(page, minimum_price)

        _notify(progress, "uploading_details", "상세페이지와 고정 상·하단 이미지를 넣는 중입니다.")
        uploaded, country, notes = _configure_images_and_details(
            page,
            context,
            job,
            fixed_top_paths=fixed_top_paths,
            fixed_bottom_paths=fixed_bottom_paths,
            watermark_settings=watermark_settings,
            work_root=work_root,
        )
        notes = option_notes + address_notes + notes

        page.bring_to_front()
        # 상세에디터 저장 과정에서 WING이 카테고리 입력 영역을 다시 그리면
        # 앞서 선택한 인증 예외가 풀릴 수 있다. 최종 저장 직전에 재적용한다.
        _ensure_purchase_agent_certification(page)
        meta_defaults = _ensure_product_meta_defaults(page)
        if meta_defaults:
            notes.append("상품 주요 정보 기본값 보완: " + ", ".join(meta_defaults))

        if deferred_option_matching:
            _notify(
                progress,
                "creating_inventory_id",
                "필수정보 입력을 마쳐 쿠팡 임시 상품 ID를 만든 뒤 옵션 매칭을 재시도합니다.",
            )
            _create_temporary_inventory_id(page)
            retry_notes = _complete_missing_required_options(
                page,
                matched,
                product_id=str(job.get("product_id") or ""),
                _allow_intermediate_save=False,
            )
            _fill_option_prices_and_stock(page, matched)
            _disable_auto_price_adjustment(page)
            notes.extend(["임시 상품 ID 생성 후 옵션 매칭 완료", *retry_notes])

        message = (
            f"자동입력 완료: 옵션 {len(matched)}개, 최저 매칭가 {minimum_price:,}원, "
            f"재고 999개, 출고 9일, 반품배송비 {return_fee:,}원"
        )
        if notes:
            message += " / " + ", ".join(notes)
        if not country:
            message += " / 실제 원산지는 최종 실행 전에 확인 필요"
        if job.get("translation_mode") == "auto_ko" and not job.get(
            "translation_review_approved"
        ):
            message += " / 한국어 번역·교정 승인본이 없어 원본 상세 이미지를 사용"
        _notify(progress, "awaiting_confirmation", message)
        # CDP로 연결한 브라우저는 Playwright 연결이 끝나도 열린 채 유지된다.
        return {
            "matched_options": matched,
            "minimum_selling_price": minimum_price,
            "return_shipping_fee": return_fee,
            "country_of_origin": country,
            "uploaded_detail_count": uploaded,
            "message": message,
        }


def submit_prepared_job(
    job: dict,
    *,
    profile_dir: Path,
    port: int = DEFAULT_DEBUG_PORT,
    progress: ProgressCallback | None = None,
) -> dict:
    """사용자 확인 후 준비된 WING 폼의 실제 저장/등록 버튼을 누른다."""

    from playwright.sync_api import sync_playwright

    def apply_visible_return_fee_limit(
        page,
        current_fee: int,
        *,
        force_fallback: bool = False,
    ) -> int | None:
        alerts = page.locator(".sweet-alert:visible")
        messages: list[str] = []
        has_visible_alert = bool(alerts.count())
        if has_visible_alert:
            messages.append(" ".join(alerts.last.inner_text().split()))
        if force_fallback:
            messages.append("반품배송비 입력 확인")

        # WING은 상품등록 팝업에는 '반품/교환 항목 확인'만 표시하고,
        # 실제 허용 상한(예: 0원~30,000원)은 입력칸 아래에 따로 표시한다.
        # 팝업과 반품/교환 영역을 함께 읽어야 제한 금액을 자동 보정할 수 있다.
        try:
            label = _last_visible(page.get_by_text("반품배송비(편도)", exact=True))
            section = label.locator(
                'xpath=ancestor::div[contains(@class,"form-section")][1]'
            )
            messages.append(" ".join(section.inner_text().split()))
        except Exception:
            pass

        target_fee = _return_fee_adjustment_target(
            current_fee,
            messages,
            allow_generic_fallback=has_visible_alert or force_fallback,
        )
        if target_fee is None or target_fee == current_fee:
            return None
        confirmation = page.locator(
            "button.alert-confirm:visible, .sweet-alert:visible button.confirm"
        )
        if confirmation.count():
            confirmation.last.click(force=True)
            page.wait_for_timeout(400)
        # SweetAlert를 닫은 뒤 실제 반품비 입력칸을 다시 찾아 값을 넣고,
        # WING이 다른 기본값으로 되돌리지 않았는지까지 확인한다.
        _set_return_fee_value(page, target_fee)
        _notify(
            progress,
            "return_fee_adjusted",
            f"WING 허용 범위에 맞춰 반품배송비를 {target_fee:,}원으로 자동 보정했습니다.",
        )
        confirmation = page.locator(
            "button.alert-confirm:visible, .sweet-alert:visible button.confirm"
        )
        if confirmation.count():
            confirmation.last.click(force=True)
        page.wait_for_timeout(400)
        return target_fee

    launch_controlled_chrome(profile_dir=profile_dir, port=port)
    with sync_playwright() as playwright:
        browser = _connect_controlled_chrome(
            playwright,
            profile_dir=profile_dir,
            port=port,
        )
        _context, page = _wing_page(browser)
        page.bring_to_front()
        mode = str(job.get("submission_mode") or "")
        button_name = "임시 저장" if mode == "draft" else "상품등록"
        actual_return_fee = int(job.get("return_shipping_fee") or 0)
        if actual_return_fee < 0:
            raise WingAutomationError("저장된 반품배송비 값이 올바르지 않습니다.")
        # 준비 단계 이후 WING의 React 화면이 다시 그려져도 카테고리별
        # 구매대행 인증과 제주/도서산간 배송 가능 선택이 유지되도록 제출
        # 직전에 한 번 더 확인한다.
        _ensure_purchase_agent_certification(page)
        _ensure_remote_area_shipping_available(page)
        _disable_auto_price_adjustment(page)
        # 이전 제출의 반품비 제한 팝업이 그대로 열려 있을 수 있으므로 먼저
        # 팝업에 표시된 실제 최댓값을 읽고 닫는다. 팝업이 없을 때만 저장된
        # 금액을 다시 넣어 WING이 기본값으로 되돌린 경우를 복구한다.
        adjusted_fee = apply_visible_return_fee_limit(page, actual_return_fee)
        if adjusted_fee is not None:
            actual_return_fee = adjusted_fee
        else:
            _set_return_fee_value(page, actual_return_fee)
        _ensure_purchase_agent_certification(page)
        _ensure_product_meta_defaults(page)
        _notify(progress, "submitting", f"WING에서 {button_name}을 실행하는 중입니다.")
        primary_targets = page.locator("button.fs-unmask").filter(
            has_text=re.compile(rf"^\s*{re.escape(button_name)}\s*$")
        )
        target = (
            _last_visible(primary_targets)
            if primary_targets.count()
            else _visible_button_with_text(
                page, re.compile(rf"^\s*{re.escape(button_name)}\s*$")
            )
        )
        if target.is_disabled() and actual_return_fee > RETURN_FEE_FALLBACK_CAP:
            # 이전 제출에서 일반적인 '반품/교환 항목 확인'만 남고 팝업이
            # 닫혔더라도, 비활성화된 등록 버튼을 만나면 10만원으로 복구한다.
            adjusted_fee = apply_visible_return_fee_limit(
                page,
                actual_return_fee,
                force_fallback=True,
            )
            if adjusted_fee is not None:
                actual_return_fee = adjusted_fee
                page.wait_for_timeout(500)
        if target.is_disabled():
            raise WingAutomationError(
                f"WING {button_name} 버튼이 비활성화되어 있습니다. 화면의 필수항목을 확인하세요."
            )
        old_url = page.url

        def wait_for_submission_response() -> None:
            """느리게 표시되는 WING 검증 팝업 또는 확인창을 기다린다."""

            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if page.url != old_url:
                    return
                if _post_save_choice_visible(page):
                    return
                if page.locator(".sweet-alert:visible").count():
                    return
                final_buttons = page.locator("button.wuic-button").filter(
                    has_text=re.compile(r"^\s*상품등록\s*$")
                )
                if any(
                    final_buttons.nth(index).is_visible()
                    for index in range(final_buttons.count())
                ):
                    return
                page.wait_for_timeout(100)

        _click_reliably(target)
        wait_for_submission_response()

        visible_alerts = page.locator(".sweet-alert:visible")
        alert_text = ""
        # 상품·카테고리·배송조건별 반품비 상한은 WING 검증 팝업이 가장
        # 정확하다. 상한을 읽어 값을 낮추고 실제 입력값을 확인한 뒤 최대
        # 세 번까지 다시 제출한다.
        for _attempt in range(3):
            if not visible_alerts.count():
                alert_text = ""
                break
            alert_text = " ".join(visible_alerts.last.inner_text().split())
            adjusted_fee = apply_visible_return_fee_limit(page, actual_return_fee)
            if adjusted_fee is None or adjusted_fee == actual_return_fee:
                break
            actual_return_fee = adjusted_fee
            target = _last_visible(primary_targets)
            _click_reliably(target)
            wait_for_submission_response()
            visible_alerts = page.locator(".sweet-alert:visible")

        if visible_alerts.count():
            alert_text = " ".join(visible_alerts.last.inner_text().split())
            if alert_text and "반품배송비" in alert_text:
                confirmation = page.locator(
                    "button.alert-confirm:visible, .sweet-alert:visible button.confirm"
                )
                if confirmation.count():
                    confirmation.last.click(force=True)
                raise WingAutomationError(
                    "반품배송비를 WING 허용 상한으로 자동 보정했지만 다시 거부되었습니다: "
                    f"{alert_text[:700]}"
                )
            validation_tokens = (
                "입력항목",
                "확인해주세요",
                "필수항목",
                "상세설명 항목",
                "입력해주세요",
            )
            if alert_text and any(token in alert_text for token in validation_tokens):
                missing_meta = _product_meta_missing_fields(page)
                confirmation = page.locator(
                    "button.alert-confirm:visible, .sweet-alert:visible button.confirm"
                )
                if confirmation.count():
                    confirmation.last.click()
                detail = (
                    " / 누락된 상품 주요 정보: " + ", ".join(missing_meta)
                    if missing_meta
                    else ""
                )
                raise WingAutomationError(
                    f"{button_name} 검증 실패: {alert_text[:700]}{detail}"
                )

        def click_submission_confirmation() -> bool:
            """판매요청 확인창이 있으면 마지막 확인 버튼을 한 번 누른다."""

            if mode != "draft":
                # 판매요청 확인창의 버튼은 일반 form 버튼과 달리 wuic-button을
                # 사용한다. 문구 DOM 구조가 바뀌어도 이 버튼을 직접 찾는다.
                final_buttons = page.locator("button.wuic-button").filter(
                    has_text=re.compile(r"^\s*상품등록\s*$")
                )
                visible_final_buttons = [
                    final_buttons.nth(index)
                    for index in range(final_buttons.count())
                    if final_buttons.nth(index).is_visible()
                ]
                if visible_final_buttons:
                    final_button = visible_final_buttons[-1]
                    if not final_button.is_disabled():
                        _click_reliably(final_button)
                        return True

            confirmation_names = (
                ("저장", "확인")
                if mode == "draft"
                else ("상품등록", "등록", "확인")
            )
            confirmation_containers = page.locator(
                ".modal:visible, .sweet-alert:visible"
            )
            if not confirmation_containers.count():
                return False
            container = confirmation_containers.last
            for name in confirmation_names:
                buttons = container.locator("button").filter(
                    has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")
                )
                visible = [
                    buttons.nth(i)
                    for i in range(buttons.count())
                    if buttons.nth(i).is_visible()
                ]
                if visible:
                    candidate = visible[-1]
                    if not candidate.is_disabled():
                        _click_reliably(candidate)
                        return True
            return False

        click_submission_confirmation()

        # WING은 첫 상품등록 버튼이 아니라 마지막 판매요청 확인 버튼을 누른
        # 뒤에야 반품비 상한 팝업을 띄우기도 한다. 이 두 번째 검증 시점도
        # 기다렸다가 상한을 자동 반영하고 등록 과정을 처음부터 다시 누른다.
        late_corrections = 0
        while True:
            response_deadline = time.monotonic() + 8
            alert_text = ""
            while time.monotonic() < response_deadline:
                if page.url != old_url:
                    break
                late_alerts = page.locator(".sweet-alert:visible")
                if late_alerts.count():
                    alert_text = " ".join(late_alerts.last.inner_text().split())
                    break
                page.wait_for_timeout(100)

            if not alert_text:
                break

            adjusted_fee = apply_visible_return_fee_limit(page, actual_return_fee)
            if (
                adjusted_fee is not None
                and adjusted_fee != actual_return_fee
                and late_corrections < 3
            ):
                actual_return_fee = adjusted_fee
                late_corrections += 1
                target = _last_visible(primary_targets)
                _click_reliably(target)
                wait_for_submission_response()
                if not page.locator(".sweet-alert:visible").count():
                    click_submission_confirmation()
                continue

            confirmation = page.locator(
                "button.alert-confirm:visible, .sweet-alert:visible button.confirm"
            )
            if confirmation.count():
                confirmation.last.click(force=True)
            if "반품배송비" in alert_text:
                raise WingAutomationError(
                    "반품배송비를 WING 허용 상한으로 자동 보정했지만 다시 거부되었습니다: "
                    f"{alert_text[:700]}"
                )
            missing_meta = _product_meta_missing_fields(page)
            detail = (
                " / 누락된 상품 주요 정보: " + ", ".join(missing_meta)
                if missing_meta
                else ""
            )
            raise WingAutomationError(
                f"{button_name} 검증 실패: {alert_text[:700]}{detail}"
            )

        _notify(
            progress,
            "waiting_final_save",
            "WING 최종 저장 응답을 기다리는 중입니다. 상품 수에 따라 최대 90초 걸릴 수 있습니다.",
        )
        deadline = time.monotonic() + FINAL_SUBMISSION_WAIT_SECONDS
        success_text = ""
        while time.monotonic() < deadline:
            page.wait_for_timeout(500)
            body = page.locator("body").inner_text()
            markers = (
                "임시저장되었습니다",
                "임시 저장되었습니다",
                "상품이 등록되었습니다",
                "상품등록이 완료",
                "등록 완료",
            )
            success_text = next((marker for marker in markers if marker in body), "")
            if not success_text and _post_save_choice_visible(page):
                success_text = "저장 후 이동 선택창"
            if success_text or page.url != old_url:
                break

        body = page.locator("body").inner_text()
        if not success_text and page.url == old_url:
            alert_messages: list[str] = []
            alerts = page.locator(".sweet-alert .alert-text")
            for index in range(alerts.count()):
                try:
                    alert_messages.append(alerts.nth(index).inner_text())
                except Exception:
                    continue
            messages = _submission_diagnostic_messages(body, alert_messages)
            detail = " / ".join(messages)[:900]
            raise WingAutomationError(
                f"{button_name}은 눌렀지만 완료되지 않았습니다. WING 확인: {detail or '화면의 빨간 안내를 확인하세요.'}"
            )

        registered_id = ""
        match = re.search(r"(?:등록상품|상품)\s*ID\s*[:：]?\s*([0-9]+)", body)
        if match:
            registered_id = match.group(1)
        _notify(
            progress,
            "opening_next_registration",
            "저장을 마쳐 새 상품등록 화면으로 이동하는 중입니다.",
        )
        _open_new_registration_after_success(page)
        _notify(
            progress,
            "completed",
            f"WING {button_name} 완료 / 다음 상품 등록 화면 준비 완료",
        )
        return {
            "registered_product_id": registered_id,
            "return_shipping_fee": actual_return_fee,
            "message": f"WING {button_name} 완료 / 다음 상품 등록 화면 준비 완료",
        }
