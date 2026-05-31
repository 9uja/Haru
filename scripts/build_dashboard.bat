@echo off
chcp 65001 >nul
title HaruBot Dashboard 빌더
REM ============================================================
REM PyInstaller 로 HaruBotDashboard.exe 를 빌드한다.
REM 결과: dist\HaruBotDashboard.exe (단일 실행 파일, 약 10~20 MB)
REM 사용: 이 파일을 더블클릭. 또는 PowerShell/cmd 에서 실행.
REM ============================================================
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv 가 없습니다. 먼저 의존성을 설치하세요:
    echo   python -m venv .venv
    echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "dashboard.py" (
    echo [ERROR] dashboard.py 가 프로젝트 루트에 없습니다.
    pause
    exit /b 1
)

echo PyInstaller 설치/업데이트 확인...
.\.venv\Scripts\python.exe -m pip install --upgrade --quiet pyinstaller
if errorlevel 1 (
    echo [ERROR] PyInstaller 설치 실패.
    pause
    exit /b 1
)

echo.
echo HaruBotDashboard.exe 빌드 중... (몇 분 걸릴 수 있어요)
echo.

.\.venv\Scripts\python.exe -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name HaruBotDashboard ^
    --clean ^
    --noconfirm ^
    --distpath dist ^
    --workpath build ^
    --specpath build ^
    dashboard.py

if not exist "dist\HaruBotDashboard.exe" (
    echo.
    echo [ERROR] 빌드 실패. 위 로그를 확인하세요.
    pause
    exit /b 1
)

echo.
echo ════════════════════════════════════════════════════════
echo  빌드 완료!
echo ────────────────────────────────────────────────────────
for %%I in (dist\HaruBotDashboard.exe) do echo  파일: %%~fI  (%%~zI bytes)
echo ────────────────────────────────────────────────────────
echo  사용법:
echo   1) dist\HaruBotDashboard.exe 를 더블클릭 (GUI 열림)
echo   2) "시작" 버튼으로 봇 켜기, "중지" 로 끄기
echo   3) ".env 편집" 탭에서 토큰/DB URL 설정
echo   4) "호스팅" 탭에서 자동 재시작/자동 시작 토글
echo.
echo  바탕화면 바로가기 만들기:
echo   dist\HaruBotDashboard.exe 우클릭 → 보내기 → 바탕화면(바로가기 만들기)
echo ════════════════════════════════════════════════════════
pause
