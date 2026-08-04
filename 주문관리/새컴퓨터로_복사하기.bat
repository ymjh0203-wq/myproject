@echo off
REM ============================================================
REM  주문관리 - 다른 컴퓨터로 옮기기 위한 복사 도구
REM
REM  USB나 외장하드 등 원하는 위치로 이 프로그램을 깨끗하게
REM  복사합니다. ".venv" 폴더(380MB, 이 컴퓨터 전용이라 옮겨도
REM  작동하지 않음)와 파이썬 캐시는 자동으로 제외됩니다.
REM
REM  이 파일은 "복사"만 합니다. 무엇도 지우지 않습니다.
REM
REM  주의: 아래 chcp 65001 줄을 지우지 마세요.
REM ============================================================
chcp 65001 >nul
setlocal
title 주문관리 - 다른 컴퓨터로 복사
cd /d "%~dp0"

echo ============================================================
echo    주문관리를 다른 컴퓨터로 복사하기
echo ============================================================
echo.

set "DEST=%~1"
if not defined DEST (
    echo  어디에 복사할까요?
    echo  예:  E:\주문관리
    echo.
    set /p "DEST=복사할 위치: "
)

if not defined DEST goto :no_dest

echo.
echo  [!] 이 폴더에는 실제 API 키^(.env^)가 들어 있습니다.
echo      데이터베이스에는 고객 실명, 전화번호, 통관고유부호 같은
echo      실제 개인정보가 들어 있습니다. 취급에 주의하세요.
echo.
set /p "INCDB=데이터베이스도 함께 복사할까요? (y = 예 / n = 빈 상태로 시작): "
echo.

set "DBSWITCH="
if /i not "%INCDB%"=="y" set "DBSWITCH=*.db *.db-journal"

echo  복사 위치: %DEST%
echo  잠시 기다려주세요...
echo.

robocopy "%CD%" "%DEST%" /E /NFL /NDL /NJH /NP ^
  /XD ".venv" "__pycache__" ".git" ".claude" ".pytest_cache" ^
  /XF "*.pyc" %DBSWITCH%

if %errorlevel% geq 8 goto :copy_failed

echo.
echo ============================================================
echo    복사가 끝났습니다.
echo ============================================================
echo.
echo  이제 옮겨갈 컴퓨터에서:
echo    1. 복사한 폴더를 바탕화면처럼 단순한 위치에 두세요.
echo       OneDrive 폴더 안에는 두지 마세요.
echo    2. "설치.bat" 을 더블클릭하고 끝날 때까지 기다리세요.
echo    3. 그 컴퓨터의 인터넷 주소^(IP^)를 쿠팡 Wing에 등록하세요.
echo       상점 3개 각각 등록해야 합니다.
echo    4. "주문관리_실행.bat" 을 더블클릭하면 프로그램이 켜집니다.
echo.
echo  자세한 설명은 복사된 폴더 안 "새컴퓨터_설치안내.md" 에 있습니다.
echo.
if /i not "%INCDB%"=="y" (
    echo  [!] 데이터베이스를 복사하지 않았으므로 새 컴퓨터는 빈 상태로
    echo      시작합니다. 설정 화면에서 상점을 다시 등록하셔야 합니다.
    echo.
)
pause
exit /b 0

:no_dest
echo.
echo [오류] 복사할 위치를 입력하지 않으셨습니다. 아무것도 복사하지 않았습니다.
echo.
pause
exit /b 1

:copy_failed
echo.
echo [오류] 복사를 끝내지 못했습니다.
echo        복사할 드라이브가 연결되어 있는지, 남은 용량이 충분한지
echo        확인한 뒤 다시 시도하세요.
echo.
pause
exit /b 1
