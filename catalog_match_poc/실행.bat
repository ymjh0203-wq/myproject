@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Run the Setup file first.
    pause
    exit /b 1
)

start "" wscript.exe "%~dp0쿠팡상품등록_실행.vbs"
exit /b 0
