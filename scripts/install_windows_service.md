# Windows 서비스로 HaruBot 호스팅 (NSSM)

NSSM(Non-Sucking Service Manager) 로 봇을 Windows 서비스로 등록.
부팅 시 자동 시작, 백그라운드 실행(콘솔 없음), 크래시 시 자동 재시작.

## 1) NSSM 다운로드

https://nssm.cc/download → 최신 버전 zip → 압축 해제

`nssm-2.24/win64/nssm.exe` 를 적당한 곳에 둡니다. 예: `C:\Tools\nssm.exe`

## 2) 서비스 등록 (GUI 방식)

관리자 권한 PowerShell 또는 cmd 에서:

```cmd
C:\Tools\nssm.exe install HaruBot
```

GUI 창이 뜨면 아래 값들을 입력:

| 탭 | 항목 | 값 |
|---|---|---|
| **Application** | Path | `C:\Users\blok1\Documents\Claude\Projects\HaruBot\.venv\Scripts\python.exe` |
| **Application** | Startup directory | `C:\Users\blok1\Documents\Claude\Projects\HaruBot` |
| **Application** | Arguments | `bot.py` |
| **Details** | Display name | `HaruBot Discord Bot` |
| **Details** | Description | `HaruBot Discord 봇 (단일 길드)` |
| **Details** | Startup type | `Automatic` |
| **I/O** | Output (stdout) | `C:\Users\blok1\Documents\Claude\Projects\HaruBot\logs\service.log` |
| **I/O** | Error (stderr) | `C:\Users\blok1\Documents\Claude\Projects\HaruBot\logs\service.log` |
| **Exit actions** | On Exit / Restart action | `Restart application` |
| **Exit actions** | Delay restart by | `5000` ms |

`Install service` 버튼 클릭.

## 3) 서비스 등록 (명령어 방식, 한 번에)

또는 GUI 없이:

```cmd
C:\Tools\nssm.exe install HaruBot ^
    "C:\Users\blok1\Documents\Claude\Projects\HaruBot\.venv\Scripts\python.exe" ^
    "bot.py"

C:\Tools\nssm.exe set HaruBot AppDirectory ^
    "C:\Users\blok1\Documents\Claude\Projects\HaruBot"

C:\Tools\nssm.exe set HaruBot Description "HaruBot Discord 봇"
C:\Tools\nssm.exe set HaruBot Start SERVICE_AUTO_START
C:\Tools\nssm.exe set HaruBot AppStdout "C:\Users\blok1\Documents\Claude\Projects\HaruBot\logs\service.log"
C:\Tools\nssm.exe set HaruBot AppStderr "C:\Users\blok1\Documents\Claude\Projects\HaruBot\logs\service.log"
C:\Tools\nssm.exe set HaruBot AppRotateFiles 1
C:\Tools\nssm.exe set HaruBot AppRotateBytes 10485760
```

## 4) 서비스 시작

```cmd
sc start HaruBot
```

또는 `services.msc` → `HaruBot` → 시작.

## 5) 일상 명령

```cmd
sc query HaruBot      # 상태 확인
sc stop HaruBot       # 중지
sc start HaruBot      # 시작
C:\Tools\nssm.exe restart HaruBot   # 재시작
C:\Tools\nssm.exe edit HaruBot      # 설정 GUI
C:\Tools\nssm.exe remove HaruBot    # 서비스 제거
```

## 6) 라이브 로그 보기

```powershell
Get-Content -Path "C:\Users\blok1\Documents\Claude\Projects\HaruBot\logs\service.log" -Wait -Tail 50
```

(Linux 의 `tail -f` 와 동일)

## 7) 부팅 fingerprint 4개 로그 확인

```
cogs.leveling : Leveling cog 로드 — 메시지 XP 1/60s, 음성 XP 30/5min...
cogs.stats_rpg: StatsRPG cog 로드 — 레벨당 4포인트...
cogs.bump     : Bump cog 로드 — 역할 기반 알림 활성...
cogs.raid     : Raid cog 로드 — Phase 3.5 (보스 3종, 특성 6종, ...)
```

## 주의 사항

- **컴퓨터 절전 모드 방지**: 설정 → 전원 → "절전 모드로 전환: 안 함" 설정 필요
- **Windows Update**: 자동 재부팅으로 봇 잠시 끊김 가능 (서비스라 자동 복구)
- **인터넷 끊김**: 봇이 자동으로 재연결 시도 (Discord 게이트웨이 RESUME)
- **방화벽**: Windows Defender 가 첫 실행 시 묻는 경우 "허용" — 단 봇은 outbound only 이므로 inbound 규칙 불필요
- **파일 권한**: `.env` 의 비밀이 다른 윈도우 사용자에게 노출 안 되도록 우클릭 → 속성 → 보안 → 본인만 읽기 권한 권장
