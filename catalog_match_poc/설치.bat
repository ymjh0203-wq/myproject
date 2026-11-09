@echo off
setlocal
chcp 65001 >nul
title Coupang Bulk Registration Helper - Setup
cd /d "%~dp0"

set "PYCMD="
py -3.12 --version >nul 2>&1
if not errorlevel 1 set "PYCMD=py -3.12"
if defined PYCMD goto :found_python

py -3 --version >nul 2>&1
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto :found_python

python --version >nul 2>&1
if not errorlevel 1 set "PYCMD=python"
if defined PYCMD goto :found_python

set "BUNDLED_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%BUNDLED_PY%" set "PYCMD="%BUNDLED_PY%""
if defined PYCMD goto :found_python
goto :no_python

:found_python
echo [1/3] Checking Python...
%PYCMD% --version
%PYCMD% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto :old_python
if /i "%~1"=="--check" goto :check_ok

echo [2/3] Creating the private environment...
if not exist ".venv\Scripts\python.exe" goto :create_environment
".venv\Scripts\python.exe" --version >nul 2>&1
if not errorlevel 1 goto :install_packages
echo Existing environment is broken. Rebuilding it...
%PYCMD% -m venv --clear .venv
if errorlevel 1 goto :failed
goto :install_packages

:create_environment
%PYCMD% -m venv .venv
if errorlevel 1 goto :failed

:install_packages
echo [3/3] Installing the screen library...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Setup completed. Double-click the Run file in this folder.
pause
exit /b 0

:check_ok
echo Setup script check: OK
exit /b 0

:no_python
echo.
echo [ERROR] Python was not found.
echo Install Python 3.12 from https://www.python.org/downloads/windows/
echo Select "Add python.exe to PATH" during installation, then try again.
pause
exit /b 1

:old_python
echo.
echo [ERROR] Python 3.10 or newer is required. Python 3.12 is recommended.
pause
exit /b 1

:failed
echo.
echo [ERROR] Setup failed. Check the messages above and your internet connection.
pause
exit /b 1
