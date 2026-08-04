@echo off
title Order Management (server)
cd /d "%~dp0"

REM ============================================================
REM  Order Management launcher
REM  - Starts the Streamlit server in headless mode (no browser tab)
REM  - Opens a standalone app window (no address bar / no tabs)
REM    using Edge or Chrome in "--app" mode, so it looks and feels
REM    like a normal desktop program instead of a web site.
REM  NOTE: keep this file ASCII-only. Korean text here breaks cmd parsing.
REM ============================================================

echo ============================================
echo   Starting Order Management Program
echo ============================================
echo.

REM --- stop any leftover server still holding the port ---
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

if not exist ".venv\Scripts\streamlit.exe" (
    echo.
    echo [ERROR] Required program files not found.
    echo         Please make sure this file is inside the "Order Management" folder.
    echo.
    pause
    exit /b 1
)

REM --- start the server in the background, without opening a browser tab ---
REM  (keep this on ONE line: a "^" continuation breaks the start command)
start "Order Management server" /min ".venv\Scripts\streamlit.exe" run app.py --server.headless=true --server.port=8501

echo Starting the program window...

REM --- wait until the server can actually SERVE the page (max ~45 seconds) ---
REM  Checking the port alone is not enough: Streamlit opens the port a few
REM  seconds before it can serve app.py, so the browser used to open too early
REM  and show a blank / "cannot connect" page (had to launch twice).
REM  Streamlit's health endpoint returns "ok" only when it is truly ready.
set READY=
for /l %%i in (1,1,90) do (
    if not defined READY (
        curl -s -m 2 http://localhost:8501/_stcore/health 2>nul | findstr /i "ok" >nul 2>&1
        if not errorlevel 1 set READY=1
        if not defined READY ping -n 1 -w 500 127.0.0.1 >nul 2>&1
    )
)

if not defined READY (
    echo.
    echo [ERROR] The program did not start in time.
    echo         Please close this window and try again.
    echo.
    pause
    exit /b 1
)

REM --- small grace period so the first page render is ready before we open ---
ping -n 3 -w 500 127.0.0.1 >nul 2>&1

REM --- open a standalone app window (own profile so it stays separate) ---
REM  Prefer Chrome (open in a Chrome app window). Chrome can be installed in a
REM  few different folders, so we check all of them before falling back to Edge.
REM  IMPORTANT: Chrome and Edge each get their OWN profile folder. If they share
REM  one folder, whichever browser is still open locks it and the other one
REM  fails to launch (this is why it kept opening in Edge).
set CHROME=
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set CHROME="%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set CHROME="%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set CHROME="%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
set EDGE="%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"

if defined CHROME (
    start "" %CHROME% --app=http://localhost:8501 --user-data-dir="%LOCALAPPDATA%\OrderManagementApp_Chrome" --window-size=1600,950
) else if exist %EDGE% (
    echo Chrome not found. Opening in an Edge app window instead.
    start "" %EDGE% --app=http://localhost:8501 --user-data-dir="%LOCALAPPDATA%\OrderManagementApp" --window-size=1600,950
) else (
    echo Could not find Chrome or Edge. Opening in the default browser instead.
    start "" http://localhost:8501
)

echo.
echo The program window is open.
echo Keep this small window open while you work - closing it stops the program.
echo.
pause

REM --- stop the server when this window is closed via the prompt above ---
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
