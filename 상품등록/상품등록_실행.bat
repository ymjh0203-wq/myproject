@echo off
cd /d "%~dp0"

REM Kill any previous server holding port 8503 (prevents stale duplicates)
for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":8503" ^| findstr "LISTENING"') do taskkill /f /pid %%p >nul 2>&1

set "URL=http://localhost:8503"
set "CHROME=chrome"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

start "" powershell -WindowStyle Hidden -Command "Start-Sleep 4; Start-Process '%CHROME%' '%URL%'"

.venv\Scripts\python.exe -m streamlit run app.py --server.headless=true --server.port 8503

pause
