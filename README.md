# HaruBot

단일 디스코드 서버(길드) 전용 봇. Python + [discord.py](https://github.com/Rapptz/discord.py) 기반이며,
슬래시 커맨드로 동작합니다.

## 기능

| 명령어 | 설명 | 권한 |
| --- | --- | --- |
| `/ping` | 봇 응답 지연(왕복·게이트웨이) 확인 | 누구나 |
| `/setup-log [이름]` | 봇 전용(비공개) 로그 채널 생성 | 채널 관리 |
| `/inactive [일수]` | 음성 비활성 멤버를 임베드로 조회 (◀▶ 페이지·🔃 정렬) | 서버 관리 |
| `/activity` | 전체 멤버 음성 활동을 임베드로 조회 (◀▶ 페이지·🔃 정렬) | 서버 관리 |
| `/stats [멤버]` | 멤버 통계(서버 입·퇴장 횟수, 누적 음성 체류시간, 최근 활동) | 누구나 |
| `/휴면 표시 [일수] [dm]` | 비활성 멤버에게 '휴면' 역할 부여 (+선택 DM 경고) | 역할 관리 |
| `/휴면 해제` | 모든 '휴면' 역할 해제 | 역할 관리 |
| `하루야 <메시지>` | "하루야"로 시작하면 AI가 대화로 응답 (Gemini) | 누구나 |
| `하루야 번역 <문장>` | 한국어↔영어 자동 번역 (`하루야 번역 일본어 …` 로 지정 가능) | 누구나 |

> 위 슬래시 명령은 영어 이름(`/ping`, `/dormant set·clear` 등)으로도 제공됩니다.
> AI 대화/번역은 슬래시가 아니라 채팅에 `하루야 …` 로 말하면 됩니다.
> '휴면' 역할은 해당 멤버가 음성에 다시 입장하면 **자동 해제**됩니다.

### 음성 활동 추적 & 비활성 안내
- 음성 채널 입장/퇴장을 로그 채널에 기록하고, 멤버별 마지막 활동 시각·누적 체류시간을 **PostgreSQL** 에 영속화.
- 서버(길드) 입·퇴장(나갔다 재입장)을 감지해 멤버별 입·퇴장 횟수를 누적 기록(`/stats` 로 조회).
- 한 달(기본 30일) 이상 음성 활동이 없는 멤버를 `/inactive` 또는 **자동 정기 보고**(기본 매주)로 안내.
- 안내 목록은 `@username`(클릭 가능·무음 멘션) 형식이라 관리자가 바로 상호작용 가능.

## 빠른 시작

```powershell
# 1. 의존성 설치 (가상환경 권장)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 환경 변수 설정 (토큰, 길드 ID, DATABASE_URL 입력)
Copy-Item .env.example .env

# 3. 실행
.\.venv\Scripts\python.exe bot.py
```

> 음성 활동 저장에 PostgreSQL이 필요합니다. 무료 [Neon](https://neon.tech) DB 한 개면 충분합니다.
> 토큰 발급, DB 준비, 인텐트/권한, 봇 초대 방법은 [docs/SETUP.md](docs/SETUP.md)를 참고하세요.

## 프로젝트 구조

```
HaruBot/
├── bot.py              # 진입점: 봇 클래스, 코그 로드, 길드 동기화, DB/헬스서버 수명관리
├── config.py           # .env 로딩 및 설정값
├── database.py         # PostgreSQL 데이터 계층 (asyncpg)
├── views.py            # 인터랙티브 임베드 페이지네이터 (◀▶ 페이지·🔃 정렬 버튼)
├── keepalive.py        # (선택) PaaS 헬스체크용 HTTP 서버 (PORT 있을 때만 작동)
├── requirements.txt
├── Dockerfile          # 컨테이너 배포용 (VM/Docker 공통)
├── start.sh            # 무료 봇 패널(Wispbyte 등)용 시작 스크립트
├── .env.example        # 환경 변수 템플릿 (.env 는 git 제외)
├── deploy/
│   └── harubot.service # systemd 유닛 (VM 24시간 구동·자동 재시작)
├── cogs/               # 기능 모듈
│   ├── general.py      # /ping (핑)
│   ├── voice_log.py    # 음성/멤버 추적, /setup-log·/inactive·/activity·/stats, 자동 보고
│   └── ai_chat.py      # "하루야 …" 메시지 → Gemini 무료 API 대화
└── docs/
    ├── SETUP.md        # 토큰 발급·DB·실행·초대 가이드
    ├── DEPLOY.md       # Oracle Cloud 무료 VM + Neon 24시간 배포 가이드
    ├── ARCHITECTURE.md # 기술 결정·구조 설명
    └── PROGRESS.md     # 개발 진행 로그
```

## 환경

- Python 3.14 / discord.py 2.7.1 / asyncpg 0.31 에서 확인됨 (배포 컨테이너는 Python 3.12)
- 데이터 저장: 외부 PostgreSQL(예: Neon 무료 티어). `DATABASE_URL` 필요
- 음악(보이스 재생) 기능은 사용하지 않으므로 `[voice]` extra 불필요
- 24시간 무료 운영(카드 불필요): **Wispbyte 무료 패널 + Neon Postgres**.
  더 견고하게는 Oracle Cloud 무료 VM. 자세한 비교/절차는 [docs/DEPLOY.md](docs/DEPLOY.md)
