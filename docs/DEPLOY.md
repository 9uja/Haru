# 배포 가이드 — 무료 24시간 호스팅

봇 용량이 작아(코드 ~64KB, 의존성 ~수십 MB, RAM ~100MB 내외) 무료 저사양 호스팅으로 충분합니다.
데이터는 외부 **Neon Postgres**(무료, 카드 불필요)에 저장하므로 호스트가 바뀌거나 재시작돼도 보존됩니다.

| 옵션 | 카드 | 난이도 | 견고함 |
| --- | --- | --- | --- |
| **A. Wispbyte 무료 패널** | 불필요 | 쉬움 | 보통 |
| **B. Oracle Cloud 무료 VM** | 필요 | 보통 | 높음 |

> 둘 다 DB는 Neon을 공유합니다. 카드 없이 진행하려면 **A + Neon**.

---

## 0. (공통) Neon 데이터베이스 — 카드 불필요

1. [neon.tech](https://neon.tech) 가입(신용카드 없이 가능) → 프로젝트 생성
2. **Connection string** 복사: `postgresql://user:pass@ep-xxx-pooler.<region>.aws.neon.tech/dbname?sslmode=require`
3. 뒤에서 `DATABASE_URL` 로 사용 (`?sslmode=require` 유지). 테이블은 봇 최초 기동 시 자동 생성.

---

## A. Wispbyte 무료 패널 (카드 불필요, 권장)

Wispbyte/HeavenCloud 등은 Pterodactyl 기반 패널로, 코드를 올리고 시작 명령만 지정하면 됩니다.

### A-1. 서버 생성
1. [wispbyte.com](https://wispbyte.com) 가입(카드 불필요)
2. 새 서버 생성 → 유형 **Python** 선택 (가능하면 Python 3.12)

### A-2. 코드 업로드
- 패널 **File Manager** 로 프로젝트 파일 업로드(드래그&드롭 또는 ZIP 업로드 후 압축 해제),
  또는 SFTP 접속해 업로드. (`.venv/` 는 올리지 말 것 — 패널이 의존성을 새로 설치)
- GitHub 연동(Pull)을 지원하면 레포 URL로 받아도 됩니다.

### A-3. 환경 변수(.env) 작성
File Manager 로 서버 루트에 `.env` 파일을 만들고 값을 채웁니다(`.env.example` 참고):

```dotenv
DISCORD_TOKEN=발급받은_봇_토큰
GUILD_ID=대상_서버_ID
DATABASE_URL=postgresql://user:pass@host/dbname?sslmode=require
INACTIVE_DAYS=30
REPORT_INTERVAL_HOURS=168
LOG_LEVEL=INFO
```

### A-4. 시작 파일 지정 후 실행
Wispbyte의 Python egg 는 `requirements.txt` 를 **자동 설치**한 뒤 `python <지정파일>` 을 실행합니다.
- **Startup** 탭에서 **Python 파일(`PY_FILE`)** 값을 **`bot.py`** 로 지정.
  - ⚠️ `start.sh` 로 지정하면 egg 가 `python start.sh` 로 실행해 **SyntaxError** 가 납니다.
    이 패널에서는 `start.sh` 가 필요 없습니다(셸 명령형 startup 을 쓰는 다른 패널 전용).
- **Start** → 콘솔에 `DB 연결 완료`, `슬래시 커맨드 9개 동기화`, `로그인: ...` 이 보이면 성공.

### A-5. 업데이트
파일을 다시 올리거나 Git pull 후, 패널에서 **Restart**.

---

## B. Oracle Cloud 평생 무료 VM (카드 필요, 더 견고)

리눅스 VM에서 `systemd` 로 상시 구동. 신용카드 인증이 필요하지만 자원이 넉넉하고 안정적입니다.

### B-1. VM 생성
1. [Oracle Cloud](https://www.oracle.com/cloud/free/) 가입(카드 인증, Always Free 한도 내 무과금)
2. **Compute > Instances > Create**: Ubuntu 24.04 / Shape는 **Ampere A1 (ARM)** Always Free 사양 / SSH 키 등록
3. Public IP 확인 (인바운드 포트 개방 불필요)

> ⚠️ Oracle은 장기 유휴 무료 인스턴스를 회수할 수 있음 → 데이터를 Neon(외부)에 두므로 안전.

### B-2. 서버 준비 & 실행
```bash
ssh ubuntu@<PUBLIC_IP>
sudo apt update && sudo apt install -y python3-venv python3-pip git
git clone https://github.com/<계정>/<레포>.git HaruBot && cd HaruBot
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
nano .env          # 위 A-3 와 동일한 값 입력
.venv/bin/python bot.py   # 수동 기동 확인 후 Ctrl+C
```

### B-3. systemd 등록(24시간 자동 구동)
```bash
sudo cp deploy/harubot.service /etc/systemd/system/harubot.service   # User/경로 환경에 맞게 수정
sudo systemctl daemon-reload
sudo systemctl enable --now harubot
journalctl -u harubot -f
```

업데이트: `git pull && sudo systemctl restart harubot`

---

## 참고

- **저사양 최적화**: `chunk_guilds_at_startup=False`(시작 시 전체 멤버 캐시 미수신, 필요 시 로드) +
  작은 DB 풀(max 3)로 RAM/연결을 아낍니다. 256MB급 패널에서도 동작.
- 패널 저장공간(예: Wispbyte 1GB)이 재시작에도 유지된다면 Neon 대신 로컬 SQLite로 단순화도 가능하지만,
  견고함(이전/재설치 대비)을 위해 외부 Neon 유지를 권장합니다. 원하면 SQLite 버전으로 변경 가능.
