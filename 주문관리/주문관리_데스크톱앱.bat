@echo off
title Order Management (desktop app)
cd /d "%~dp0"

REM ============================================================
REM  Order Management - Desktop App launcher (pywebview)
REM  - Opens the program in a standalone native window (no browser,
REM    no address bar, its own taskbar icon).
REM  - First run installs 'pywebview' into the local .venv.
REM  NOTE: keep this file ASCII-only (Korean here breaks cmd parsing).
REM ============================================================

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run this inside the Order Management folder.
    pause
    exit /b 1
)

REM --- install pywebview on first run (only if missing) ---
.venv\Scripts\python.exe -c "import webview" 1>nul 2>nul
if errorlevel 1 (
    echo Installing the desktop window component ^(pywebview^)... one-time setup.
    .venv\Scripts\python.exe -m pip install pywebview
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install pywebview. Check your internet connection.
        pause
        exit /b 1
    )
)

REM --- launch the desktop window without a console (pythonw) ---
start "" ".venv\Scripts\pythonw.exe" desktop_app.py
exit /b 0
