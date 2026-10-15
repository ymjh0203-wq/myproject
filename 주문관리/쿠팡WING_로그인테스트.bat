@echo off
chcp 65001 >nul
title Coupang WING login automation (test)
cd /d "%~dp0"

REM ============================================================
REM  Coupang WING login automation - STEP 1 feasibility test
REM  - Opens Chrome, you log into WING yourself (incl. 2FA).
REM  - Session is saved to a dedicated profile for reuse.
REM  - First run installs 'playwright' into the local .venv.
REM  NOTE: keep this file ASCII-only (Korean breaks cmd parsing).
REM ============================================================

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run inside the Order Management folder.
    pause
    exit /b 1
)

.venv\Scripts\python.exe -c "import playwright" 1>nul 2>nul
if errorlevel 1 (
    echo Installing browser automation component ^(playwright^)... one-time setup.
    .venv\Scripts\python.exe -m pip install playwright
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install playwright. Check your internet connection.
        pause
        exit /b 1
    )
)

echo.
echo A Chrome window will open. Log into Coupang WING there (do 2FA if asked),
echo then open the ORDER LIST page and leave it. This window will report progress.
echo.

.venv\Scripts\python.exe -m integrations.coupang_wing_automation login

echo.
echo Done. You can close this window.
pause
