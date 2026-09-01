@echo off
cd /d "%~dp0"

set "URL=http://localhost:8503"
set "CHROME=chrome"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

start "" powershell -WindowStyle Hidden -Command "Start-Sleep 4; Start-Process '%CHROME%' '%URL%'"

.venv\Scripts\python.exe -m streamlit run app.py --server.headless=true --server.port 8503

pause
