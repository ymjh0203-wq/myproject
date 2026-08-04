@echo off
REM ============================================================
REM  주문관리 - 새 컴퓨터 최초 설치
REM  폴더를 다른 PC로 복사한 뒤 이 파일을 "한 번만" 실행하세요.
REM  파이썬을 찾고, 가상환경을 만들고, 필요한 패키지를 설치합니다.
REM
REM  주의: 아래 chcp 65001 줄을 지우지 마세요.
REM        이 줄이 없으면 cmd가 한글 다음 줄부터 명령을 잘못 읽습니다.
REM ============================================================
chcp 65001 >nul
title 주문관리 - 설치
cd /d "%~dp0"

echo ============================================================
echo    주문관리  -  설치
echo    (이 컴퓨터에서 딱 한 번만 실행하면 됩니다)
echo ============================================================
echo.

REM ------------------------------------------------------------
REM  1. 사용 가능한 파이썬 찾기
REM ------------------------------------------------------------
set "PYCMD="

py -3.12 --version >nul 2>&1
if %errorlevel%==0 set "PYCMD=py -3.12"
if defined PYCMD goto :found_python

py -3 --version >nul 2>&1
if %errorlevel%==0 set "PYCMD=py -3"
if defined PYCMD goto :found_python

python --version >nul 2>&1
if %errorlevel%==0 set "PYCMD=python"
if defined PYCMD goto :found_python

goto :no_python

:found_python
echo [1/4] 파이썬을 찾았습니다:
%PYCMD% --version
echo.

REM --- 3.10 이상인지 확인 ---
%PYCMD% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto :old_python

REM --- 폴더 경로가 너무 길면 설치 도중 실패하므로 미리 막습니다 ---
REM     (윈도우 경로 260자 제한. streamlit 안에 아주 깊은 폴더가 있습니다)
%PYCMD% -c "import os,sys; sys.exit(1 if len(os.getcwd())>90 else 0)" >nul 2>&1
if errorlevel 1 goto :path_too_long

REM ------------------------------------------------------------
REM  2. 가상환경 만들기
REM ------------------------------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo [2/4] 가상환경이 이미 있어서 그대로 사용합니다.
) else (
    echo [2/4] 가상환경을 만드는 중입니다... ^(약 30초^)
    %PYCMD% -m venv .venv
    if errorlevel 1 goto :venv_failed
)
echo.

REM ------------------------------------------------------------
REM  3. 패키지 설치
REM ------------------------------------------------------------
echo [3/4] 필요한 패키지를 설치하는 중입니다.
echo       인터넷 속도에 따라 3~10분 걸립니다.
echo       글자가 계속 올라가는 것이 정상이니 창을 닫지 마세요.
echo.
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :pip_failed
echo.

REM ------------------------------------------------------------
REM  4. 설정 파일 확인
REM ------------------------------------------------------------
set "ENV_WAS_CREATED="
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        set "ENV_WAS_CREATED=1"
    )
)
echo [4/4] 설정 파일 확인을 마쳤습니다.
echo.

echo ============================================================
echo    설치가 정상적으로 끝났습니다.
echo ============================================================
echo.

if defined ENV_WAS_CREATED (
    echo  [!] 중요 - API 키 파일 ^(.env^) 이 없어서 빈 양식만 만들었습니다.
    echo      원래 쓰던 컴퓨터에서 ".env" 파일을 복사해 이 폴더에
    echo      넣어주세요. 넣지 않으면 수집하기가 전부 실패합니다.
    echo.
)

echo  다음으로 하실 일:
echo    1. ".env" 와 "order_management.db" 를 원래 컴퓨터에서
echo       복사해 이 폴더에 넣었는지 확인하세요.
echo    2. 이 컴퓨터의 인터넷 주소^(IP^)를 쿠팡 Wing에 등록하세요.
echo       상점 3개 각각 등록해야 합니다. 안 하면 전부 403 오류입니다.
echo    3. "주문관리_실행.bat" 을 더블클릭해서 프로그램을 켜세요.
echo.
echo  자세한 설명은 "새컴퓨터_설치안내.md" 파일에 있습니다.
echo.
pause
exit /b 0


REM ============================================================
REM  오류 안내
REM ============================================================
:no_python
echo.
echo [오류] 이 컴퓨터에 파이썬이 설치되어 있지 않습니다.
echo.
echo   해결 방법:
echo     1. https://www.python.org/downloads/windows/ 접속
echo     2. "Python 3.12.x  Windows installer (64-bit)" 다운로드
echo     3. 설치 화면 맨 아래 체크박스를 반드시 체크하세요:
echo            "Add python.exe to PATH"
echo     4. 설치가 끝나면 이 파일을 다시 실행하세요.
echo.
pause
exit /b 1

:old_python
echo.
echo [오류] 설치된 파이썬 버전이 너무 낮습니다.
echo        파이썬 3.10 이상이 필요합니다.
echo        python.org 에서 3.12 를 설치한 뒤 다시 실행하세요.
echo.
pause
exit /b 1

:path_too_long
echo.
echo [오류] 이 폴더의 경로가 너무 깁니다.
echo.
echo        현재 위치:
echo        %CD%
echo.
echo        윈도우는 경로 길이에 260자 제한이 있어서, 폴더가 깊은
echo        곳에 있으면 설치 도중 실패합니다.
echo.
echo        해결 방법: 이 폴더를 바탕화면처럼 짧은 위치로 옮긴 뒤
echo        다시 실행하세요.
echo.
pause
exit /b 1

:venv_failed
echo.
echo [오류] 가상환경을 만들지 못했습니다.
echo        가장 흔한 원인: 이 폴더가 OneDrive 안이나 네트워크
echo        드라이브에 있는 경우입니다.
echo        폴더를 바탕화면으로 옮긴 뒤 다시 실행해보세요.
echo.
pause
exit /b 1

:pip_failed
echo.
echo [오류] 패키지 설치에 실패했습니다.
echo        인터넷 연결을 확인한 뒤 이 파일을 다시 실행하세요.
echo        회사 방화벽이 pypi.org 를 막고 있다면 네트워크 담당자에게
echo        허용을 요청하셔야 합니다.
echo.
pause
exit /b 1
