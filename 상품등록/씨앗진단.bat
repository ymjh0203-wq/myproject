@echo off
cd /d "%~dp0"
chcp 65001 >nul
echo.
set /p URL="벤치마킹할 상품 URL 붙여넣고 Enter: "
echo.
.venv\Scripts\python.exe diag_seed.py "%URL%"
echo.
pause
