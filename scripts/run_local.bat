@echo off
chcp 65001 >nul
title HaruBot
REM ============================================================
REM HaruBot — Windows 로컬 호스팅 (자동 재시작 루프)
REM 사용법: 이 파일 더블클릭. 또는 PowerShell/cmd 에서 실행.
REM 종료: 창을 닫거나 Ctrl+C.
REM ============================================================
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv 가 없습니다. requirements.txt 설치가 필요해요.
    echo   python -m venv .venv
    echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist ".env" (
    echo [ERROR] .env 파일이 없습니다. .env.example 을 복사해서 채워주세요.
    pause
    exit /b 1
)

if not exist "logs" mkdir logs

:loop
echo.
echo [%date% %time%] HaruBot 시작...
.\.venv\Scripts\python.exe bot.py
echo [%date% %time%] HaruBot 종료 (exit code %errorlevel%). 5초 후 재시작...
timeout /t 5 /nobreak >nul
goto loop
