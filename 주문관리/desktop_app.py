# ==========================================================
# 데스크톱 앱 실행기 (desktop_app.py)
# ----------------------------------------------------------
# 주문관리를 '브라우저 탭'이 아니라 독립된 프로그램 창(pywebview)으로 띄웁니다.
# 윈도우 작업표시줄에 자체 아이콘으로 뜨고, 주소창·탭이 없어 일반 프로그램처럼
# 보입니다. (Windows에서는 Edge WebView2 런타임을 사용 — Win11에 기본 내장)
#
# 하는 일:
#   1) Streamlit 서버가 안 떠 있으면 조용히(창 없이) 띄우고 준비될 때까지 대기
#   2) 그 주소(localhost)를 가리키는 네이티브 창을 연다
#   3) 창을 닫으면, 이 실행기가 띄운 서버는 함께 종료한다
#      (이미 떠 있던 서버를 재사용한 경우엔 건드리지 않음)
#
# 실행: 주문관리_데스크톱앱.bat  (pywebview 자동 설치 후 창 실행)
# ==========================================================

import os
import sys
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8501
HEALTH_URL = f"http://localhost:{PORT}/_stcore/health"
# ?client=desktop : 앱이 '데스크톱 창에서 열렸다'를 알도록 표시(브라우저와 UI를 다르게 하려고).
APP_URL = f"http://localhost:{PORT}/?client=desktop"


def _server_up() -> bool:
    """Streamlit health 엔드포인트가 'ok'를 주면 준비된 것."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2) as resp:
            return resp.status == 200 and b"ok" in resp.read().lower()
    except Exception:
        return False


def _python_exe() -> str:
    """서버 실행에 쓸 python.exe 경로. (pythonw로 실행됐으면 python.exe로 바꿔줌 —
    streamlit은 콘솔 출력이 필요해서 pythonw로 돌리면 문제가 생길 수 있음)"""
    py = sys.executable
    if py.lower().endswith("pythonw.exe"):
        cand = os.path.join(os.path.dirname(py), "python.exe")
        if os.path.exists(cand):
            return cand
    return py


def _start_server():
    """Streamlit 서버를 창 없이 백그라운드로 시작. 시작한 프로세스를 돌려줍니다."""
    import subprocess

    creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    env = dict(os.environ)
    env["ORDER_MGMT_DESKTOP"] = "1"  # 이 서버는 '데스크톱 앱용'임을 표시(UI 분기용)
    return subprocess.Popen(
        [_python_exe(), "-m", "streamlit", "run", "app.py",
         "--server.headless=true", f"--server.port={PORT}",
         "--browser.gatherUsageStats=false"],
        cwd=BASE_DIR,
        creationflags=creationflags,
        env=env,
    )


def _set_app_user_model_id() -> None:
    """윈도우 작업표시줄에서 python이 아니라 '주문관리'로 따로 묶이게 합니다
    (바탕화면 바로가기의 아이콘이 작업표시줄에도 그대로 보이도록)."""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OrderManagement.Desktop.1")
        except Exception:
            pass


def main() -> None:
    os.chdir(BASE_DIR)
    _set_app_user_model_id()
    import webview  # pywebview

    proc = None
    if not _server_up():
        proc = _start_server()
        for _ in range(120):  # 최대 약 60초 대기
            if _server_up():
                break
            time.sleep(0.5)

    icon_path = os.path.join(BASE_DIR, "assets", "app_icon.ico")

    # ★pywebview는 '앱처럼' 보이게 하려고 텍스트 선택(드래그·복사)을 기본으로 끕니다.
    #   주문번호·상품명 등을 끌어서 복사해야 하므로 text_select=True로 켭니다.
    #   (옛 버전은 이 인자가 없어 TypeError → 그땐 아래 CSS 주입으로 보완)
    try:
        window = webview.create_window("주문관리", APP_URL, width=1600, height=950, text_select=True)
    except TypeError:
        window = webview.create_window("주문관리", APP_URL, width=1600, height=950)

    # 보강: 페이지가 로드될 때마다 '텍스트 선택 허용' CSS를 강제로 넣습니다.
    _SELECT_CSS = ("*{-webkit-user-select:text !important;"
                   "-ms-user-select:text !important;user-select:text !important;}")

    def _enable_text_select():
        try:
            window.load_css(_SELECT_CSS)
        except Exception:
            try:
                window.evaluate_js(
                    "(function(){var s=document.createElement('style');"
                    "s.innerHTML=" + repr(_SELECT_CSS) + ";"
                    "document.head.appendChild(s);})();"
                )
            except Exception:
                pass

    try:
        window.events.loaded += _enable_text_select
    except Exception:
        pass

    try:
        # pywebview 버전에 따라 start(icon=...) 지원이 달라, 지원하면 쓰고 아니면 그냥 엽니다.
        if os.path.exists(icon_path):
            try:
                webview.start(icon=icon_path)
            except TypeError:
                webview.start()
        else:
            webview.start()
    finally:
        # 이 실행기가 띄운 서버만 종료합니다(재사용한 서버는 그대로 둠).
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
