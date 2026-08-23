@echo off
cd /d "%~dp0"
chcp 65001 >nul
.venv\Scripts\python.exe set_db_password.py
echo.
pause
