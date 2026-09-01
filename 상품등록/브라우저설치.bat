@echo off
cd /d "%~dp0"
echo ============================================================
echo  Playwright 브라우저(Chromium) 설치
echo  - 1~2분 걸립니다. 다운로드가 끝날 때까지 기다려주세요.
echo ============================================================
echo.
.venv\Scripts\python.exe -m playwright install chromium
echo.
echo 설치가 끝났으면, 상품등록_실행.bat 을 실행해서 다시 시도하세요.
echo.
pause
