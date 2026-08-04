# ==========================================================
# 퀵스타 배대지 웹 자동화 (integrations/quickstar_automation.py)
# ----------------------------------------------------------
# 샵마인처럼, 퀵스타 배송대행 신청 웹폼을 자동으로 열고 수취인정보를 채웁니다.
# Playwright로 "이 컴퓨터에 설치된 크롬"을 몰아서 조작합니다.
#
# 보안: 로그인은 판매자님이 직접 합니다(비밀번호를 코드가 절대 다루지 않음).
# 전용 프로필 폴더에 로그인 상태가 저장되어, 다음부터는 자동으로 로그인된
# 상태로 열립니다.
#
# 이 파일은 두 가지로 씁니다:
#   1) 앱에서 함수로 호출 (fill_application)
#   2) 명령줄에서 직접 실행:
#        python -m integrations.quickstar_automation login   # 최초 1회 로그인
#        python -m integrations.quickstar_automation dump     # 폼 구조 확인(개발용)
# ==========================================================

import json
import os
import sys

# 자동화 전용 크롬 프로필(로그인 상태 저장). 앱 창(--app)이나 판매자님 일반
# 크롬과 겹치지 않게 별도 폴더를 씁니다.
PROFILE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "OrderManagementApp_QuickstarBot"
)

QUICKSTAR_HOME = "https://quickstar.co.kr/"
# 배송대행 신청 페이지. 로그인 상태에서 이 주소로 바로 들어가면 신청 폼이 뜹니다.
# ('배송대행 신청하기' 링크가 이 주소를 가리킴 - 2026-07-28 확인)
QUICKSTAR_APPLY = "https://quickstar.co.kr/service/service_03.php"


class QuickstarAutomationError(Exception):
    pass


def _clear_profile_locks() -> None:
    """비정상 종료로 남은 프로필 락 파일을 지웁니다(크롬 재실행 충돌 방지)."""
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(PROFILE_DIR, lock))
        except OSError:
            pass


def _launch(headless: bool):
    """설치된 크롬을 전용 프로필로 띄웁니다. (playwright 컨텍스트를 돌려줍니다)"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise QuickstarAutomationError(
            "playwright가 설치되어 있지 않습니다. '.venv\\Scripts\\python -m pip install playwright' 로 설치하세요."
        )
    _clear_profile_locks()
    pw = sync_playwright().start()
    try:
        context = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            channel="chrome",
            headless=headless,
            args=["--start-maximized"],
            no_viewport=True,
        )
    except Exception as error:
        pw.stop()
        raise QuickstarAutomationError(f"크롬을 띄우지 못했습니다: {error}")
    return pw, context


def open_for_login() -> None:
    """
    (최초 1회) 퀵스타 로그인용으로 크롬 창을 띄웁니다. 판매자님이 이 창에서 직접
    로그인하면, 로그인된 것을 자동으로 감지해서 알려주고(LOGGED_IN) 저장합니다.
    반드시 '자동로그인'을 체크하고 로그인해야 다음에도 유지됩니다.
    """
    import time

    pw, context = _launch(headless=False)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(QUICKSTAR_HOME, timeout=30000)
        print("이 창에서 퀵스타에 로그인하세요. ('자동로그인' 꼭 체크!)", flush=True)

        # 최대 10분간, 로그인됐는지 4초마다 확인합니다. (창은 열어둔 채로)
        deadline = time.time() + 600
        detected = False
        while time.time() < deadline:
            try:
                if page.is_closed():
                    break
                if "로그아웃" in page.content():
                    detected = True
                    break
            except Exception:
                pass
            time.sleep(4)

        if detected:
            print("LOGGED_IN", flush=True)
            # 로그인된 이 세션에서 2단계 폼의 HTML을 통째로 저장합니다(구조 파악용).
            try:
                # 1단계: 선택 페이지(service_03.php)
                page.goto(QUICKSTAR_APPLY, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=30000)
                with open(os.path.join(PROFILE_DIR, "step1_select.html"), "w", encoding="utf-8") as fp:
                    fp.write(page.content())
                print("STEP1_SAVED", flush=True)

                # 2단계로 넘어가기: 제출 버튼(okok_btn) 클릭 → 수취인 입력 폼
                try:
                    page.click("input.okok_btn, .okok_btn", timeout=8000)
                    page.wait_for_load_state("networkidle", timeout=30000)
                    time.sleep(1)
                    with open(os.path.join(PROFILE_DIR, "step2_apply.html"), "w", encoding="utf-8") as fp:
                        fp.write(page.content())
                    print(f"STEP2_SAVED url={page.url}", flush=True)
                except Exception as e2:
                    print(f"STEP2_FAILED: {e2}", flush=True)
            except Exception as error:
                print(f"DUMP_FAILED: {error}", flush=True)
            time.sleep(2)
        else:
            print("NOT_DETECTED", flush=True)
    except Exception as error:
        print(f"ERROR: {error}", flush=True)
    finally:
        try:
            context.close()
        except Exception:
            pass
        pw.stop()


def is_logged_in() -> bool:
    """전용 프로필이 퀵스타에 로그인된 상태인지 확인합니다."""
    pw, context = _launch(headless=True)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(QUICKSTAR_HOME, timeout=30000)
        # 로그인되어 있으면 '로그아웃' 링크가, 아니면 '로그인'이 보입니다.
        body = page.content()
        return "로그아웃" in body
    finally:
        try:
            context.close()
        except Exception:
            pass
        pw.stop()


def _open_application_form(page):
    """배송대행 신청 폼으로 들어갑니다. (로그인 상태에서 주소로 바로 이동)"""
    page.goto(QUICKSTAR_APPLY, timeout=30000)
    page.wait_for_load_state("networkidle", timeout=30000)
    if "로그아웃" not in page.content():
        raise QuickstarAutomationError(
            "퀵스타에 로그인되어 있지 않습니다. 먼저 '퀵스타 자동화 설정(최초 1회 로그인)'을 해주세요."
        )


def dump_form() -> str:
    """(개발용) 신청 폼의 입력요소들을 뽑아 문자열로 돌려줍니다. 셀렉터 파악에 씁니다."""
    pw, context = _launch(headless=True)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        _open_application_form(page)
        fields = page.eval_on_selector_all(
            "input, select, textarea",
            """els => els.map(e => ({
                tag: e.tagName, type: e.type||'', name: e.name||'', id: e.id||'',
                placeholder: e.placeholder||'', cls: (e.className||'').slice(0,60)
            }))""",
        )
        return json.dumps(fields, ensure_ascii=False, indent=2)
    finally:
        try:
            context.close()
        except Exception:
            pass
        pw.stop()


def fill_application(receiver: dict, transport_name: str = "") -> dict:
    """
    배송대행 신청 폼을 열고 수취인정보를 자동으로 채웁니다. (제출은 하지 않습니다 -
    품목은 판매자님이 직접 넣고 검토 후 제출하시게 창은 열어둡니다)

    receiver 키: name, phone, zip, addr1, addr2, pccc
    ※ 실제 폼 셀렉터는 dump_form()으로 확인한 뒤 여기서 정확히 채워야 합니다.
      지금은 셀렉터 확정 전이라, 확인되면 이 부분을 채웁니다.
    """
    raise QuickstarAutomationError(
        "폼 자동입력은 아직 셀렉터 확정 전입니다. 로그인 설정 후 폼 구조를 확인하면 완성됩니다."
    )


COMMAND_FILE = os.path.join(PROFILE_DIR, "command.json")
RESULT_FILE = os.path.join(PROFILE_DIR, "result.json")
STATUS_FILE = os.path.join(PROFILE_DIR, "daemon_status.txt")


def _write_status(text: str) -> None:
    try:
        os.makedirs(PROFILE_DIR, exist_ok=True)
        with open(STATUS_FILE, "w", encoding="utf-8") as fp:
            fp.write(text)
    except Exception:
        pass


def _close_blank_tabs(context, keep_page) -> None:
    """작업 중 생긴 빈 탭(about:blank 등)을 닫아 화면을 깔끔하게 유지합니다."""
    for p in list(context.pages):
        try:
            if p is not keep_page and (p.url in ("about:blank", "") or not p.url):
                p.close()
        except Exception:
            pass


def _handle_command(context, active, cmd: dict) -> dict:
    """
    데몬이 받은 명령 하나를 처리합니다.
    active는 [현재활성페이지] 리스트입니다. fill은 매번 '새 탭'에서 처리해서
    이전 접수 폼이 다음 접수를 막지 않게 합니다(2번째부터 안 되던 문제 해결).
    """
    import time as _t

    page = active[0]
    action = cmd.get("action")
    try:
        if action == "fill":
            r = cmd.get("receiver", {})
            transport = str(cmd.get("transport") or "")

            # 이전 접수 폼에서 나갈 때 막히지 않도록 beforeunload를 제거합니다.
            try:
                page.evaluate("try{window.onbeforeunload=null;window.onunload=null;}catch(e){}")
            except Exception:
                pass

            # 선택 페이지로 이동 → 제출 → 수취인 입력 폼. 곧장 이동이 막히면(이전 폼 상태 등)
            # 홈을 한 번 거친 뒤 다시 시도합니다. networkidle(느림) 대신 필요한 요소가
            # 나타나면 바로 진행합니다.
            def _open_form():
                # wait_until="load"로 스크립트까지 로드 완료를 보장(제출이 먹히게).
                page.goto(QUICKSTAR_APPLY, wait_until="load", timeout=25000)
                # 작성 중이던 신청서로 바로 들어와 수취인 칸이 이미 떠 있으면 그대로 씁니다.
                # (이게 없으면 이미 폼인데 '다음' 버튼을 또 누르려다 2번째부터 실패합니다)
                try:
                    page.wait_for_selector("#gr_name", state="visible", timeout=2500)
                    return
                except Exception:
                    pass
                # 선택 페이지(step1)면 '다음' 제출에 필요한 필수 체크박스 3개를 먼저 맞춰줍니다.
                # ntform01_submit 검증: or_type_check(주의사항 동의) / or_de_no(배대지) /
                # or_to_code(받는국가). 받는국가 목록은 배대지 선택 후 AJAX로 뒤늦게 로딩되므로,
                # 그게 DOM에 뜰 때까지 기다린 뒤 체크해야 합니다(안 그러면 '받는국가를 선택해주세요'
                # alert로 막혀 폼이 넘어가지 않습니다).
                try:
                    page.wait_for_selector("input[name='or_to_code']", state="attached", timeout=10000)
                except Exception:
                    pass
                page.evaluate(
                    """() => {
                        const on = (sel) => { const e = document.querySelector(sel); if (e && !e.checked) e.checked = true; };
                        on("input[name='or_type_check']");
                        if (!document.querySelector("input[name='or_de_no']:checked")) {
                            const d = document.querySelector("input[name='or_de_no']"); if (d) d.checked = true;
                        }
                        const t = document.querySelector("input[name='or_to_code'][value='KOR']")
                               || document.querySelector("input[name='or_to_code']");
                        if (t) t.checked = true;
                    }"""
                )
                # 선택 페이지면 '다음' 제출 → 수취인 폼.
                page.click("input.okok_btn, .okok_btn", timeout=10000)
                page.wait_for_selector("#gr_name", state="visible", timeout=15000)

            try:
                _open_form()
            except Exception:
                # 홈 경유 후 재시도(이전 폼 상태를 확실히 벗어남)
                try:
                    page.goto(QUICKSTAR_HOME, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
                _open_form()

            def _fill(selector, value):
                if value:
                    try:
                        page.fill(selector, str(value), timeout=4000)
                    except Exception:
                        pass

            _fill("#gr_name", r.get("name"))
            _fill("#sample6_postcode", r.get("zip"))
            _fill("#sample6_address", r.get("addr1"))
            _fill("#sample6_address2", r.get("addr2"))
            _fill("#gr_tel", r.get("phone"))
            _fill("#gr_unipass_no", r.get("pccc"))
            _fill("#gr_ship_memo", r.get("memo"))

            if transport:
                for v in ["34", "37", "38", "42", "43"]:
                    try:
                        page.set_checked(f"#checkboxTR_gume{v}", v == transport, timeout=3000)
                    except Exception:
                        pass

            _close_blank_tabs(context, page)
            try:
                page.bring_to_front()
            except Exception:
                pass
            return {"ok": True, "msg": "수취인정보 자동입력 완료 - 품목 넣고 검토 후 제출하세요."}

        if action == "dump1":
            page.goto(QUICKSTAR_APPLY, wait_until="domcontentloaded", timeout=30000)
            with open(os.path.join(PROFILE_DIR, "step1_select.html"), "w", encoding="utf-8") as f:
                f.write(page.content())
            return {"ok": True, "msg": "step1 saved", "url": page.url}
        if action == "dump2":
            page.click("input.okok_btn, .okok_btn", timeout=8000)
            page.wait_for_selector("#gr_name", state="visible", timeout=15000)
            with open(os.path.join(PROFILE_DIR, "step2_apply.html"), "w", encoding="utf-8") as f:
                f.write(page.content())
            return {"ok": True, "msg": "step2 saved", "url": page.url}
        if action == "verify":
            # 현재 폼의 수취인 필드에 실제로 값이 들어갔는지 읽어옵니다(검증용).
            fields = [
                ("수취인명", "#gr_name"), ("우편번호", "#sample6_postcode"),
                ("주소", "#sample6_address"), ("상세주소", "#sample6_address2"),
                ("전화", "#gr_tel"), ("통관번호", "#gr_unipass_no"),
            ]
            vals = {}
            for label, sel in fields:
                try:
                    vals[label] = page.input_value(sel, timeout=3000)
                except Exception:
                    vals[label] = "(못읽음)"
            return {"ok": True, "values": vals, "url": page.url}
        # ----- 조사/통관조회용 범용 액션 (재시작 없이 페이지를 다룰 수 있게) -----
        if action == "nav":
            # 임의 주소로 이동. 팝업(window.open/target=_blank)이 뜨면 그 페이지로 전환.
            popup_holder = {}
            def _on_popup(p):
                popup_holder["p"] = p
            context.on("page", _on_popup)
            page.goto(str(cmd.get("url") or ""), wait_until=str(cmd.get("wait") or "load"), timeout=30000)
            _t.sleep(float(cmd.get("settle") or 0))
            return {"ok": True, "url": page.url, "title": page.title()}
        if action == "eval":
            # 현재 활성 페이지(또는 최근 팝업)에서 JS 실행. 결과는 JSON 직렬화 가능해야 함.
            target = page
            for p in reversed(context.pages):
                if p.url and p.url not in ("about:blank",):
                    target = p
                    break
            active[0] = target
            res = target.evaluate(str(cmd.get("js") or "null"))
            return {"ok": True, "result": res, "url": target.url}
        if action == "html":
            target = active[0]
            sel = cmd.get("selector")
            if sel:
                el = target.query_selector(sel)
                html = el.inner_html() if el else None
            else:
                html = target.content()
            limit = int(cmd.get("limit") or 20000)
            return {"ok": True, "html": (html or "")[:limit], "url": target.url, "len": len(html or "")}
        if action == "pages":
            return {"ok": True, "pages": [p.url for p in context.pages]}
        if action == "stop":
            return {"ok": True, "msg": "stopping"}
        return {"ok": False, "msg": f"unknown action: {action}"}
    except Exception as error:
        return {"ok": False, "msg": str(error)}


def daemon() -> None:
    """
    브라우저를 계속 켜둔 채로, 명령 파일(command.json)을 지켜보다가 처리합니다.
    로그인은 최초 1회만 하면 이 프로세스가 살아있는 동안 세션이 유지됩니다.
    """
    import time as _t

    # 콘솔 인코딩(cp949)에서 특수문자 출력 시 크래시하는 것을 막습니다.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    for f in (COMMAND_FILE, RESULT_FILE):
        try:
            os.remove(f)
        except OSError:
            pass

    _write_status("STARTING")
    try:
        pw, context = _launch(headless=False)
    except Exception as error:
        _write_status("STOPPED")
        print(f"LAUNCH_FAILED: {error}", flush=True)
        return
    try:
        page = context.pages[0] if context.pages else context.new_page()
        # 확인창(alert/confirm/"페이지를 벗어나시겠습니까?" beforeunload)이 뜨면 자동으로
        # 수락해서 넘깁니다. 이게 없으면 두 번째 자동입력 때 폼에 데이터가 남아있어
        # 페이지 이동이 막힙니다.
        page.on("dialog", lambda dialog: dialog.accept())
        context.on("dialog", lambda dialog: dialog.accept())
        page.goto(QUICKSTAR_HOME, timeout=30000)
        _write_status("LOGIN_WAIT")
        print("LOGIN_WAIT", flush=True)

        # 로그인 대기 (최대 15분). 살아있다는 신호로 상태를 계속 갱신합니다(하트비트).
        deadline = _t.time() + 900
        logged_in = False
        while _t.time() < deadline:
            try:
                if page.is_closed():
                    break
                if "로그아웃" in page.content():
                    logged_in = True
                    break
            except Exception:
                pass
            _write_status("LOGIN_WAIT")  # 하트비트
            _t.sleep(3)

        if not logged_in:
            _write_status("LOGIN_TIMEOUT")
            print("LOGIN_TIMEOUT", flush=True)
            return

        _write_status("READY")
        print("READY", flush=True)

        # 명령 처리 루프. active[0]가 현재 활성 탭입니다(fill 때마다 새 탭으로 교체됨).
        active = [page]
        last_heartbeat = _t.time()
        while True:
            # 탭 하나 닫혀도 유지, 브라우저 전체(모든 탭)가 닫혀야 종료합니다.
            if len(context.pages) == 0:
                break
            if _t.time() - last_heartbeat > 5:
                _write_status("READY")  # 하트비트(파일 mtime 갱신)
                last_heartbeat = _t.time()
            if os.path.exists(COMMAND_FILE):
                try:
                    with open(COMMAND_FILE, encoding="utf-8") as fp:
                        cmd = json.load(fp)
                except Exception:
                    cmd = None
                try:
                    os.remove(COMMAND_FILE)
                except OSError:
                    pass
                if cmd:
                    result = _handle_command(context, active, cmd)
                    with open(RESULT_FILE, "w", encoding="utf-8") as fp:
                        json.dump(result, fp, ensure_ascii=False)
                    print(f"HANDLED {cmd.get('action')}: {result}", flush=True)
                    if cmd.get("action") == "stop":
                        break
            _t.sleep(0.25)  # 명령을 빨리 집어들도록 짧게 폴링
    finally:
        _write_status("STOPPED")
        try:
            context.close()
        except Exception:
            pass
        pw.stop()


# ------------------------------------------------------
# 앱(Streamlit)에서 데몬을 켜고 명령을 보내는 도우미
# ------------------------------------------------------

def get_status() -> str:
    """
    데몬 상태를 읽습니다: NONE / STARTING / LOGIN_WAIT / READY / STOPPED / LOGIN_TIMEOUT
    데몬은 살아있는 동안 상태파일을 주기적으로 갱신(하트비트)합니다. 그래서 상태가
    살아있는 값(READY/LOGIN_WAIT)인데 파일이 오래 안 바뀌었으면 죽은 것으로 봅니다.
    """
    import time as _t

    try:
        mtime = os.path.getmtime(STATUS_FILE)
        with open(STATUS_FILE, encoding="utf-8") as fp:
            status = fp.read().strip()
    except OSError:
        return "NONE"
    if status in ("READY", "LOGIN_WAIT", "STARTING") and (_t.time() - mtime) > 20:
        return "STOPPED"
    return status


def is_ready() -> bool:
    return get_status() == "READY"


def start_daemon() -> None:
    """
    자동화 데몬(상시 브라우저)을 백그라운드로 켭니다. 이미 켜져 있으면(로그인 대기/준비)
    다시 켜지 않습니다. 켜지면 크롬 창이 뜨고, 판매자님이 로그인하면 READY가 됩니다.
    """
    import subprocess

    status = get_status()
    if status in ("LOGIN_WAIT", "READY"):
        return  # 이미 실행 중(로그인 대기 또는 준비됨). STARTING은 멈춘 상태일 수 있어 재시작 허용.
    _write_status("STARTING")
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [sys.executable, "-m", "integrations.quickstar_automation", "daemon"],
        cwd=project_root,
        creationflags=creationflags,
        close_fds=True,
    )


def send_command(cmd: dict, timeout: int = 45) -> dict:
    """데몬에 명령을 보내고 결과를 기다립니다."""
    import time as _t

    if not is_ready():
        return {"ok": False, "msg": "퀵스타 자동화가 준비되지 않았습니다. 먼저 '퀵스타 자동화 시작'으로 로그인하세요."}
    try:
        os.remove(RESULT_FILE)
    except OSError:
        pass
    with open(COMMAND_FILE, "w", encoding="utf-8") as fp:
        json.dump(cmd, fp, ensure_ascii=False)
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        if os.path.exists(RESULT_FILE):
            _t.sleep(0.15)
            try:
                with open(RESULT_FILE, encoding="utf-8") as fp:
                    return json.load(fp)
            except Exception:
                pass
        _t.sleep(0.2)
    return {"ok": False, "msg": "자동화 응답 시간이 초과됐습니다. 자동화 창이 켜져 있는지 확인해주세요."}


def fill_receiver(receiver: dict, transport: str) -> dict:
    """수취인정보로 배송대행 신청 폼을 자동입력합니다(제출은 안 함)."""
    import time as _t

    # 자동화 창이 닫혀 데몬이 멈춰 있으면, 자동으로 다시 켜고 잠깐 기다립니다.
    # (로그인이 유지되어 있으면 몇 초 만에 READY가 되어 그대로 진행됩니다)
    if not is_ready():
        start_daemon()
        for _ in range(20):
            if is_ready():
                break
            _t.sleep(1)
        if not is_ready():
            return {
                "ok": False,
                "msg": "자동화 브라우저를 다시 켰습니다. 뜬 크롬 창에서 로그인한 뒤 다시 '확인'을 눌러주세요.",
            }
    return send_command({"action": "fill", "receiver": receiver, "transport": transport}, timeout=60)


# ------------------------------------------------------
# 신청내역 목록 파싱 (GR 신청번호 ↔ 수취인 ↔ 운송장)
# ------------------------------------------------------
# 접수 제출은 판매자님이 수동으로 하므로, 접수 직후 GR번호를 목록에서 수취인명으로
# 찾아 주문에 저장합니다. 신청내역(service_list.php?type=ship)은 최신순이라, 방금
# 접수한 신청서가 맨 위에 옵니다.

_APP_LIST_PARSE_JS = r"""
() => {
  const txt = document.body.innerText;
  const re = /GR(\d{10,})/g; const idxs = []; let m;
  while ((m = re.exec(txt))) idxs.push({ gr: "GR" + m[1], pos: m.index });
  const out = [];
  for (let i = 0; i < idxs.length; i++) {
    const seg = txt.slice(idxs[i].pos, i + 1 < idxs.length ? idxs[i + 1].pos : txt.length);
    const name = (seg.match(/([가-힣]{2,4})\s*\((?:해운|항공)/) || [])[1] || "";
    const inv = (seg.match(/CJ대한통운\s*(\d{10,})/) || [])[1] || "";
    out.push({ gr: idxs[i].gr, name: name, invoice: inv });
  }
  return out;
}
"""

_APP_LIST_URL = "https://quickstar.co.kr/mypage/service_list.php?type=ship"


def fetch_application_list(limit: int = 40, timeout: int = 30) -> dict:
    """
    퀵스타 신청내역(최신순)을 읽어 [{gr, name, invoice}] 목록으로 돌려줍니다.
    로그인이 풀렸으면 {ok:False, need_login:True}.
    """
    if not is_ready():
        return {"ok": False, "msg": "퀵스타 자동화가 준비되지 않았습니다(로그인 필요)."}
    nav = send_command({"action": "nav", "url": _APP_LIST_URL, "wait": "load", "settle": 2}, timeout=timeout)
    if not nav.get("ok"):
        return {"ok": False, "msg": nav.get("msg", "신청내역 이동 실패")}
    if "login.php" in (nav.get("url") or ""):
        return {"ok": False, "need_login": True, "msg": "퀵스타 로그인이 풀렸습니다. 자동화 창에서 다시 로그인해주세요."}
    res = send_command({"action": "eval", "js": _APP_LIST_PARSE_JS}, timeout=timeout)
    rows = res.get("result") or []
    return {"ok": True, "rows": rows[:limit]}


def find_gr_by_receiver(name: str, timeout: int = 30) -> dict:
    """
    신청내역에서 수취인명이 일치하는 '가장 최근' 신청서(GR)를 찾습니다.
    반환: {ok, gr, name, invoice} 또는 {ok:False, msg}. (need_login 전달)
    ※ 동명이인이 있으면 최신 것을 고르므로, 접수 직후에 부르는 게 정확합니다.
    """
    r = fetch_application_list(timeout=timeout)
    if not r.get("ok"):
        return r
    target = (name or "").strip()
    for row in r["rows"]:  # 최신순
        if (row.get("name") or "").strip() == target and target:
            return {"ok": True, "gr": row["gr"], "name": row["name"], "invoice": row.get("invoice", "")}
    return {"ok": False, "msg": f"신청내역에서 수취인 '{name}'을(를) 찾지 못했습니다. 접수가 제출됐는지 확인해주세요."}


# ------------------------------------------------------
# 통관조회(관부가세 결재통보 감지)
# ------------------------------------------------------
# 퀵스타 통관조회 팝업(/guide/unipass_delivery.php?code=<GR신청번호>&invoice)에는
# 관세청 통관 처리단계가 신청서 단위로 그대로 나옵니다. 그 목록에
# "수입(사용소비) 결재통보"가 있으면 = 관부가세가 부과/통보된 시점입니다.
# 데이터는 페이지 로드 후 AJAX로 채워지므로 채워질 때까지 잠깐 폴링합니다.

# 통관 팝업 DOM에서 진행상태/처리단계를 뽑는 JS (page.evaluate로 실행)
_CUSTOMS_PARSE_JS = r"""
() => {
  const info = {};
  document.querySelectorAll("tr").forEach(tr => {
    const th = tr.querySelector("th"), td = tr.querySelector("td");
    if (th && td && tr.querySelectorAll("td").length === 1) {
      info[(th.innerText||"").trim()] = (td.innerText||"").trim();
    }
  });
  const steps = [];
  document.querySelectorAll("tr").forEach(tr => {
    const tds = [...tr.querySelectorAll("td")].map(td => (td.innerText||"").trim());
    if (tds.length >= 3 && /^\d+$/.test(tds[0])) {
      steps.push({ no: parseInt(tds[0], 10), name: tds[1], time: tds[2] });
    }
  });
  const paid = steps.find(s => s.name.indexOf("결재통보") >= 0);
  const invoices = [...new Set([...document.querySelectorAll("a")]
    .map(a => (a.textContent||"").trim()).filter(t => /^\d{10,}$/.test(t)))];
  return {
    jinhaeng: info["진행상태"] || "",
    tonggwan: info["통관진행상태"] || "",
    poom: info["품명"] || "",
    iphang: info["입항일"] || "",
    steps: steps,
    paid_notice_time: paid ? paid.time : null,
    invoices: invoices,
  };
}
"""


def quickstar_session_alive() -> bool:
    """
    데몬이 READY일 뿐 아니라 '퀵스타 로그인이 실제로 유지되고 있는지' 확인합니다.
    (is_ready는 데몬 하트비트만 봅니다. 퀵스타 세션은 시간이 지나면 풀려 login.php로
     튕기므로, 스캔/조회 전에 이걸로 확인하는 게 안전합니다.)
    """
    if not is_ready():
        return False
    r = send_command(
        {"action": "nav", "url": "https://quickstar.co.kr/mypage/mypage.php", "wait": "load", "settle": 0.5},
        timeout=20,
    )
    return r.get("ok") and "login.php" not in (r.get("url") or "")


def fetch_customs_progress(gr_code: str, timeout: int = 25) -> dict:
    """
    GR 신청번호로 퀵스타 통관조회를 열어 통관 처리단계를 읽어옵니다.
    반환: {ok, code, jinhaeng, tonggwan, poom, iphang, steps[], paid_notice_time, invoices, in_customs}
      - paid_notice_time: '수입(사용소비) 결재통보' 처리일시(YYYY-MM-DD HH:MM:SS) 또는 None
      - in_customs: 통관 데이터가 조회됐는지(반입 전이면 False)
    관부가세 발생 판정 = paid_notice_time 가 not None.
    """
    import time as _t

    code = (gr_code or "").strip()
    if not code:
        return {"ok": False, "msg": "GR 신청번호가 없습니다.", "code": code}
    if not is_ready():
        return {"ok": False, "msg": "퀵스타 자동화가 준비되지 않았습니다(로그인 필요).", "code": code}

    url = "https://quickstar.co.kr/guide/unipass_delivery.php?code=" + code + "&invoice"
    nav = send_command({"action": "nav", "url": url, "wait": "load", "settle": 1.5}, timeout=timeout)
    if not nav.get("ok"):
        return {"ok": False, "msg": nav.get("msg", "통관조회 페이지 이동 실패"), "code": code}
    # 퀵스타 세션이 풀리면 login.php로 튕깁니다 → 명확히 알려 재로그인을 유도합니다.
    if "login.php" in (nav.get("url") or ""):
        return {
            "ok": False,
            "need_login": True,
            "code": code,
            "msg": "퀵스타 로그인이 풀렸습니다. 자동화 크롬 창에서 다시 로그인해주세요.",
        }

    # AJAX로 표가 채워질 때까지 최대 ~8초 폴링(진행상태가 채워지거나 처리단계가 생기면 완료).
    data = {}
    for _ in range(8):
        res = send_command({"action": "eval", "js": _CUSTOMS_PARSE_JS}, timeout=timeout)
        data = res.get("result") or {}
        if data.get("jinhaeng") or (data.get("steps") or []):
            break
        _t.sleep(1)

    steps = data.get("steps") or []
    return {
        "ok": True,
        "code": code,
        "jinhaeng": data.get("jinhaeng", ""),
        "tonggwan": data.get("tonggwan", ""),
        "poom": data.get("poom", ""),
        "iphang": data.get("iphang", ""),
        "steps": steps,
        "paid_notice_time": data.get("paid_notice_time"),
        "invoices": data.get("invoices") or [],
        "in_customs": bool(data.get("jinhaeng") or steps),
    }


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "login"
    if action == "daemon":
        daemon()
        sys.exit(0)
    if action == "login":
        print("퀵스타 로그인 창을 엽니다. 로그인한 뒤 창을 닫으세요...")
        open_for_login()
        print("done")
    elif action == "check":
        print("logged_in:", is_logged_in())
    elif action == "dump":
        print(dump_form())
    else:
        print("usage: python -m integrations.quickstar_automation [login|check|dump]")
