@echo off
cd /d "%~dp0"
echo ============================================================
echo  Installing Playwright Chromium browser (1-2 min)...
echo  Please wait until the download finishes.
echo ============================================================
echo.
".venv\Scripts\python.exe" -m playwright install chromium
echo.
echo Done. Now run sangpum_run.bat (or the app launcher) again.
echo.
pause
