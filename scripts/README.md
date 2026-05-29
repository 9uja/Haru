# scripts/

운영용 보조 스크립트.

## oracle_setup.sh

Oracle Cloud VM 첫 셋업 자동화.

### 사용 절차 (5분)

1. **Oracle Cloud VM 생성** (Ubuntu 22.04 LTS, Ampere A1 권장)
2. **SSH 로 접속**
3. **HaruBot ZIP 업로드** (SFTP / scp)
   ```bash
   scp HaruBot-deploy-XXXX.zip ubuntu@<VM_IP>:~/
   ssh ubuntu@<VM_IP>
   sudo apt install -y unzip
   unzip ~/HaruBot-deploy-*.zip -d ~/HaruBot
   ```
4. **`.env` 파일 작성**
   ```bash
   cp ~/HaruBot/.env.example ~/HaruBot/.env
   nano ~/HaruBot/.env   # DISCORD_TOKEN, GUILD_ID, DATABASE_URL 등 입력
   ```
5. **셋업 스크립트 실행**
   ```bash
   sudo bash ~/HaruBot/scripts/oracle_setup.sh
   ```
6. **봇 시작**
   ```bash
   sudo systemctl start harubot
   sudo journalctl -u harubot -f
   ```

스크립트가 자동으로 하는 일:
- 시스템 업데이트 + 빌드툴/libpq-dev/Python 설치
- Python 3.10+ 탐지(3.12 → 3.11 → 3.10 우선순위), 없으면 deadsnakes PPA로 3.11 설치
- 메모리 < 2GB 면 swap 2GB 자동 생성
- virtualenv 생성 + `pip install -r requirements.txt`
- `.env` 권한 600 으로 보호
- systemd 유닛 등록 + 부팅 자동 시작 활성화 + 크래시 자동 재시작

### 환경 변수 (선택)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `HARUBOT_USER` | `ubuntu` | 봇을 실행할 시스템 유저 |
| `HARUBOT_DIR` | `/home/$USER/HaruBot` | 프로젝트 디렉토리 |
| `SWAP_SIZE_MB` | `2048` | swap 파일 크기 (MB) |
| `ENABLE_SWAP` | `auto` | `auto` (RAM<2GB만 활성) / `yes` / `no` |
| `HARUBOT_AUTO_START` | `0` | `1` 로 두면 셋업 직후 자동 시작 |

예: 다른 유저로 셋업
```bash
sudo HARUBOT_USER=harubot bash scripts/oracle_setup.sh
```

예: 셋업 직후 바로 시작
```bash
sudo HARUBOT_AUTO_START=1 bash scripts/oracle_setup.sh
```

### 재실행 안전성

스크립트는 **멱등** 합니다:
- venv가 이미 있으면 그대로 두고 pip만 갱신
- systemd 유닛이 이미 있으면 새로 생성/덮어쓰기
- swap 이 이미 있으면 추가 안 함
- `.env`/DB 데이터는 절대 건드리지 않음

코드 업데이트 후 다시 실행하면 안전하게 갱신됩니다:
```bash
# 새 ZIP 풀기
unzip -o ~/HaruBot-deploy-XXXX.zip -d ~/HaruBot
# 의존성 갱신
sudo bash ~/HaruBot/scripts/oracle_setup.sh
# 재시작
sudo systemctl restart harubot
```

### 자주 보는 명령

```bash
# 상태 확인
sudo systemctl status harubot

# 라이브 로그
sudo journalctl -u harubot -f

# 최근 100줄 로그
sudo journalctl -u harubot -n 100 --no-pager

# 부팅 시간 이후 로그
sudo journalctl -u harubot --since today

# 봇 중지/시작/재시작
sudo systemctl stop harubot
sudo systemctl start harubot
sudo systemctl restart harubot

# 자동 부팅 비활성화/활성화
sudo systemctl disable harubot
sudo systemctl enable harubot
```

### 트러블슈팅

| 증상 | 원인/해결 |
|---|---|
| `pip install` wheel 빌드 실패 | 스크립트가 `build-essential`/`libpq-dev` 설치하므로 거의 발생 안 함. 그래도 실패하면 `apt install python3.11-dev` 추가 |
| `Failed to start harubot.service` | `journalctl -u harubot -e` 로 traceback 확인. 보통 `.env` 누락 또는 잘못된 토큰 |
| `Address already in use` | 다른 곳에서 봇이 이미 실행 중. `sudo systemctl stop harubot` 후 재시도 |
| 메모리 부족 (`Killed`) | swap 추가 (스크립트가 자동으로 함). 그래도 부족하면 인스턴스 RAM 올리기 |
| Discord 토큰 무효 | 토큰 재발급 → `.env` 갱신 → `sudo systemctl restart harubot` |
| Neon DB 연결 실패 | DSN 의 `?sslmode=require` 누락 / VCN egress 차단 확인 |
| Cloudflare 1015 차단 | `http_guard.py` 가 자동 대응. 봇 잠시 끄고 1~24시간 대기. 또는 Reserved IP 재할당 |
