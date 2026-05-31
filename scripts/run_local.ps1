# ============================================================
# HaruBot — Windows 로컬 호스팅 (PowerShell, 자동 재시작 + 로그)
# 실행: 우클릭 → "PowerShell로 실행"
# 또는 PowerShell 에서:  .\scripts\run_local.ps1
# 종료: 창을 닫거나 Ctrl+C.
#
# 정책 차단 시 한 번만:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# ============================================================
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host '[ERROR] .venv 가 없습니다.' -ForegroundColor Red
    Write-Host '  python -m venv .venv'
    Write-Host '  .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
    Read-Host '엔터를 누르면 종료'
    exit 1
}
if (-not (Test-Path (Join-Path $root '.env'))) {
    Write-Host '[ERROR] .env 가 없습니다. .env.example 을 복사해서 채워주세요.' -ForegroundColor Red
    Read-Host '엔터를 누르면 종료'
    exit 1
}

$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

$Host.UI.RawUI.WindowTitle = 'HaruBot'

while ($true) {
    $today = Get-Date -Format 'yyyyMMdd'
    $logFile = Join-Path $logDir "bot-$today.log"
    $startMsg = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] HaruBot 시작..."
    Write-Host $startMsg -ForegroundColor Cyan
    Add-Content -Path $logFile -Value $startMsg -Encoding utf8

    # 봇 실행 — 콘솔과 로그 파일 동시에 출력
    & $python bot.py 2>&1 | ForEach-Object {
        $_
        Add-Content -Path $logFile -Value $_ -Encoding utf8
    }

    $stopMsg = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] HaruBot 종료. 5초 후 재시작..."
    Write-Host $stopMsg -ForegroundColor Yellow
    Add-Content -Path $logFile -Value $stopMsg -Encoding utf8
    Start-Sleep -Seconds 5
}
