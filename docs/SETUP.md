# 설치 및 실행 가이드

## 1. 봇 애플리케이션 생성 & 토큰 발급

1. [Discord Developer Portal](https://discord.com/developers/applications) 접속 → **New Application**
2. 좌측 **Bot** 탭 → **Reset Token** 으로 토큰 발급 (이 토큰은 한 번만 표시됨, 외부 노출 금지)
3. 같은 화면 **Privileged Gateway Intents** 에서 아래 **둘 다** 켭니다:
   - **SERVER MEMBERS INTENT** — 멤버 입·퇴장/비활성 조회에 필요
   - **MESSAGE CONTENT INTENT** — "하루야 …" AI 대화(메시지 본문 읽기)에 필요

## 2. 봇 초대

좌측 **OAuth2 > URL Generator** 에서:

- **Scopes**: `bot`, `applications.commands`
- **Bot Permissions**: `Manage Channels`, `Manage Roles`, `Send Messages`, `Embed Links`, `Read Messages/View Channels`

> `Manage Channels` 는 `/setup-log`, `Manage Roles` 는 `/휴면`('휴면' 역할 부여/해제)에 필요합니다.
> 봇 역할이 '휴면' 역할보다 **위**에 있어야 부여됩니다(서버 설정 > 역할에서 위로 이동).
> 음성 활동 추적에 쓰는 Voice States 인텐트는 기본 인텐트라 별도 포털 설정이 필요 없습니다.

생성된 URL로 접속해 대상 서버에 초대합니다.

> 봇 역할의 위치가 **부여하려는 역할보다 위**에 있어야 `/role` 명령이 동작합니다.
> (서버 설정 > 역할 에서 HaruBot 역할을 위로 끌어올리세요.)

## 3. 서버(길드) ID 확인

1. 디스코드 **설정 > 고급 > 개발자 모드** 활성화
2. 대상 서버 아이콘 우클릭 → **서버 ID 복사**

## 4. 데이터베이스(PostgreSQL) 준비

음성 활동은 외부 PostgreSQL 에 저장합니다(호스팅 파일시스템이 휘발성이어도 데이터 보존).
무료 [Neon](https://neon.tech) 기준:

1. Neon 가입 → 프로젝트 생성 (리전은 봇 호스팅과 가까운 곳)
2. 대시보드의 **Connection string** 복사 (예: `postgresql://user:pass@ep-xxx-pooler.<region>.aws.neon.tech/dbname?sslmode=require`)
3. 이 값을 `.env` 의 `DATABASE_URL` 에 붙여넣기 (끝에 `?sslmode=require` 유지)

테이블은 봇이 처음 기동할 때 자동 생성됩니다(`guild_config`, `voice_activity`).

## 5. 환경 변수 설정

`.env.example` 을 복사해 `.env` 를 만들고 값을 채웁니다.

```dotenv
DISCORD_TOKEN=발급받은_봇_토큰
GUILD_ID=복사한_서버_ID
DATABASE_URL=postgresql://user:pass@host/dbname?sslmode=require
INACTIVE_DAYS=30
REPORT_INTERVAL_HOURS=168
LOG_LEVEL=INFO
GEMINI_API_KEY=       # (선택) AI 대화 기능용
```

### (선택) AI 대화 기능 — Google Gemini
채팅창에 **`하루야 <메시지>`** 로 말하면 AI가 답합니다(예: `하루야 오늘 기분 어때?`).
번역도 됩니다: `하루야 번역 hello` (자동), `하루야 번역 일본어 안녕하세요` (지정). 쓰려면:
1. **MESSAGE CONTENT INTENT** 활성화(위 1번 참고) — 메시지 본문을 읽어야 하므로 필수
2. [Google AI Studio](https://aistudio.google.com/apikey) 접속(신용카드 불필요) → **Create API key**
3. 발급된 키를 `.env` 의 `GEMINI_API_KEY` 에 입력 (없으면 봇은 정상 동작하되 AI만 안내 메시지 출력)
- 무료 한도: `gemini-2.5-flash-lite` 기준 분당 15회 / 하루 1,000회 정도 → 소규모 서버에 충분
- 주의: 무료 티어 입력은 모델 개선에 사용될 수 있으니 민감정보는 보내지 마세요.

## 6. 실행

```powershell
.\.venv\Scripts\python.exe bot.py
```

정상 기동 시 로그에 다음과 비슷한 내용이 출력됩니다:

```
DB 연결 완료
코그 로드됨: cogs.general
슬래시 커맨드 10개 동기화 (guild=...)
로그인: HaruBot#1234 (id=...)
```

지정한 길드에서는 슬래시 커맨드가 **즉시** 반영됩니다(글로벌 동기화와 달리 지연 없음).

## 7. 첫 사용

1. 서버에서 `/setup-log` 실행 → 봇 전용(비공개) 로그 채널이 생성됩니다.
2. 이후 음성 채널 입장/퇴장이 그 채널에 기록되고, 활동 시각이 DB에 저장됩니다.
3. `/inactive` 로 비활성 멤버를 즉시 조회하거나, 설정 주기로 자동 보고를 받습니다.

## 문제 해결

| 증상 | 원인/해결 |
| --- | --- |
| `환경 변수 'DISCORD_TOKEN' 가 설정되지 않았습니다` | `.env` 누락 또는 값 비어 있음 |
| 슬래시 커맨드가 안 보임 | `GUILD_ID` 가 실제 서버와 다름 / 봇이 그 서버에 없음 |
| `/setup-log` 실패 | 봇에 Manage Channels 권한 없음 |
| `members` 관련 오류 | Developer Portal 에서 SERVER MEMBERS INTENT 미활성화 |
| DB 연결 실패(SSL 등) | `DATABASE_URL` 끝에 `?sslmode=require` 포함 여부 확인, 자격증명/호스트 재확인 |
| 비활성 목록이 비어 보임 | 멤버 캐시 미완료 — 봇은 `guild.chunk()` 로 보강하지만, 멤버 인텐트 필수 |
