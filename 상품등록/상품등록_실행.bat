@echo off
cd /d "%~dp0"
chcp 65001 >nul

REM ============================================================
REM  Launch the app and open it in Chrome.
REM  - Streamlit runs headless (no default-browser auto-open)
REM  - Chrome is opened at the app URL after a short delay
REM  - Close THIS window to stop the server
REM ============================================================

set "URL=http://localhost:8503"

REM Find Chrome (fallback to registered 'chrome' if not found)
set "CHROME=chrome"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

REM Wait for the server, then open Chrome (separate hidden window)
start "" powershell -WindowStyle Hidden -Command "Start-Sleep 4; Start-Process '%CHROME%' '%URL%'"

REM Start the Streamlit server (headless so it won't open the default browser)
.venv\Scripts\streamlit.exe run app.py --server.headless=true --server.port 8503

pause
