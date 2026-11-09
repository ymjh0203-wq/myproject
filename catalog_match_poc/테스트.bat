@echo off
setlocal
chcp 65001 >nul
title Coupang Catalog Match Phase 0 - Test
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :test
echo Run the Setup file first.
pause
exit /b 1

:test
".venv\Scripts\python.exe" -m unittest discover -s tests -v
pause
