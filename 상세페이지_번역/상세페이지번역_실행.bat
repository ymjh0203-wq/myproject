@echo off
title Detail Page Translator
cd /d "%~dp0"

echo ============================================
echo   Starting Detail Page Translator
echo ============================================
echo.

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8502" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

if not exist ".venv\Scripts\streamlit.exe" (
    echo.
    echo [ERROR] Required program files not found.
    echo         Please make sure this file is inside the "Detail Page Translator" folder.
    echo.
    pause
    exit /b 1
)

echo Checking browser engine (only downloads once, may take a while on first run)...
".venv\Scripts\python.exe" -m playwright install chromium

echo.
echo Starting the program. Your browser will open automatically in a moment...
echo (Closing this window will stop the program. You can minimize it instead.)
echo.

".venv\Scripts\streamlit.exe" run app.py --server.port 8502

pause
