#!/usr/bin/env bash
# ==============================================================================
# HaruBot — Oracle Cloud VM 자동 셋업 스크립트
# ------------------------------------------------------------------------------
# 대상: Ubuntu 22.04 LTS 또는 24.04 LTS (Ampere ARM 또는 AMD x86)
# 권한: root (sudo bash scripts/oracle_setup.sh)
#
# 동작:
#   1. 시스템 업데이트 + 필수 패키지(빌드툴/libpq-dev 등)
#   2. Python 3.10+ 탐지/설치 (3.12 → 3.11 → 3.10 우선순위)
#   3. 메모리 < 2GB 면 swap 자동 생성
#   4. virtualenv 생성 + requirements.txt 설치
#   5. .env 권한 보호 (600)
#   6. systemd 유닛 등록 (deploy/harubot.service 활용, 없으면 자동 생성)
#   7. 자동 부팅 + 크래시 자동 재시작 설정
#
# 사전 준비:
#   - HaruBot ZIP 을 /home/ubuntu/HaruBot/ 에 풀어둘 것 (SFTP)
#   - .env 파일을 같은 폴더에 만들고 토큰/DB URL 채울 것
#
# 환경 변수(선택):
#   HARUBOT_USER       = "ubuntu" (기본)
#   HARUBOT_DIR        = "/home/$HARUBOT_USER/HaruBot" (기본)
#   SWAP_SIZE_MB       = 2048 (기본, swap 활성 시)
#   ENABLE_SWAP        = auto (auto|yes|no, 기본 auto)
#   HARUBOT_AUTO_START = 0   (1 로 두면 마지막에 systemctl start 까지)
# ==============================================================================
set -euo pipefail

HARUBOT_USER="${HARUBOT_USER:-ubuntu}"
HARUBOT_DIR="${HARUBOT_DIR:-/home/${HARUBOT_USER}/HaruBot}"
SWAP_SIZE_MB="${SWAP_SIZE_MB:-2048}"
ENABLE_SWAP="${ENABLE_SWAP:-auto}"
HARUBOT_AUTO_START="${HARUBOT_AUTO_START:-0}"
SERVICE_NAME="harubot"

# ───────── 출력 헬퍼 ─────────
RED='\033[0;31m'; YEL='\033[0;33m'; CYA='\033[0;36m'; GRN='\033[0;32m'; CLR='\033[0m'
log()     { echo -e "${CYA}[step]${CLR}  $*"; }
warn()    { echo -e "${YEL}[warn]${CLR}  $*"; }
err()     { echo -e "${RED}[err ]${CLR}  $*" >&2; }
success() { echo -e "${GRN}[ ok ]${CLR}  $*"; }

# ───────── 0. 사전 검사 ─────────
if [[ $EUID -ne 0 ]]; then
    err "root 권한이 필요합니다.  실행: sudo bash $0"
    exit 1
fi

if ! id "$HARUBOT_USER" >/dev/null 2>&1; then
    err "사용자 '$HARUBOT_USER' 가 없습니다. HARUBOT_USER 환경변수로 지정하세요."
    exit 1
fi

OS_PRETTY=$(. /etc/os-release && echo "${PRETTY_NAME:-unknown}")
ARCH=$(uname -m)
log "OS:   $OS_PRETTY"
log "Arch: $ARCH"
log "User: $HARUBOT_USER"
log "Dir:  $HARUBOT_DIR"

# ───────── 1. 시스템 업데이트 + 패키지 설치 ─────────
log "apt 캐시 갱신..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -qq

log "필수 패키지 설치..."
apt-get install -y -qq \
    build-essential \
    libpq-dev \
    git \
    curl \
    ca-certificates \
    nano \
    software-properties-common \
    python3 \
    python3-venv \
    python3-dev

# ───────── 2. Python 탐지/설치 ─────────
log "Python 인터프리터 탐색..."
PY=""
for cand in python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        # 3.10 이상 확인
        ver=$("$cand" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "")
        major=${ver%%.*}; minor=${ver#*.}
        if [[ "$major" == "3" && "$minor" -ge 10 ]]; then
            PY="$cand"
            break
        fi
    fi
done

if [[ -z "$PY" ]]; then
    # Ubuntu 22.04 면 deadsnakes PPA 에서 3.11 설치
    if grep -q "Ubuntu 22.04" /etc/os-release; then
        log "Python 3.11 설치(via deadsnakes)..."
        add-apt-repository -y ppa:deadsnakes/ppa
        apt-get update -y -qq
        apt-get install -y -qq python3.11 python3.11-venv python3.11-dev
        PY="python3.11"
    else
        err "Python 3.10+ 를 찾지 못했고 자동 설치도 실패했습니다."
        exit 1
    fi
fi

PY_VER=$("$PY" --version)
success "Python: $PY_VER  ($PY)"

# ───────── 3. swap 설정 (저메모리 보호) ─────────
RAM_MB=$(free -m | awk '/^Mem:/ {print $2}')
log "RAM: ${RAM_MB} MB"

setup_swap=false
case "$ENABLE_SWAP" in
    auto)    (( RAM_MB < 2000 )) && setup_swap=true ;;
    yes|1)   setup_swap=true ;;
    no|0)    setup_swap=false ;;
esac

if $setup_swap && [[ ! -f /swapfile ]]; then
    log "swap ${SWAP_SIZE_MB}MB 생성..."
    fallocate -l "${SWAP_SIZE_MB}M" /swapfile 2>/dev/null \
        || dd if=/dev/zero of=/swapfile bs=1M count="$SWAP_SIZE_MB" status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    success "swap 활성화"
elif [[ -f /swapfile ]]; then
    log "swap 이미 존재"
fi

# ───────── 4. 프로젝트 디렉토리 + venv ─────────
if [[ ! -d "$HARUBOT_DIR" ]]; then
    log "프로젝트 디렉토리 생성: $HARUBOT_DIR"
    mkdir -p "$HARUBOT_DIR"
    chown -R "$HARUBOT_USER:$HARUBOT_USER" "$HARUBOT_DIR"
fi

if [[ ! -f "$HARUBOT_DIR/requirements.txt" ]]; then
    warn "requirements.txt 가 없습니다."
    warn "ZIP 을 $HARUBOT_DIR 에 풀어둔 뒤 다시 실행하세요."
    warn "건너뛰려면 다시 실행 시: HARUBOT_SKIP_DEPS=1 sudo -E bash $0"
    if [[ "${HARUBOT_SKIP_DEPS:-0}" != "1" ]]; then
        exit 1
    fi
fi

if [[ -f "$HARUBOT_DIR/requirements.txt" ]]; then
    log "virtualenv 생성/갱신..."
    sudo -u "$HARUBOT_USER" "$PY" -m venv "$HARUBOT_DIR/.venv"
    sudo -u "$HARUBOT_USER" "$HARUBOT_DIR/.venv/bin/pip" install --upgrade --quiet pip
    log "requirements.txt 설치 (시간 걸릴 수 있음)..."
    sudo -u "$HARUBOT_USER" "$HARUBOT_DIR/.venv/bin/pip" install --quiet -r "$HARUBOT_DIR/requirements.txt"
    success "의존성 설치 완료"
fi

# ───────── 5. .env 권한 보호 ─────────
if [[ -f "$HARUBOT_DIR/.env" ]]; then
    chmod 600 "$HARUBOT_DIR/.env"
    chown "$HARUBOT_USER:$HARUBOT_USER" "$HARUBOT_DIR/.env"
    success ".env 권한 600 적용"
else
    warn ".env 파일이 없습니다!"
    if [[ -f "$HARUBOT_DIR/.env.example" ]]; then
        warn "다음 명령으로 만들어 채우세요:"
        warn "  cp $HARUBOT_DIR/.env.example $HARUBOT_DIR/.env"
        warn "  nano $HARUBOT_DIR/.env   # DISCORD_TOKEN, GUILD_ID, DATABASE_URL 등 입력"
    fi
fi

# ───────── 6. systemd 유닛 등록 ─────────
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PROJECT_SERVICE="$HARUBOT_DIR/deploy/harubot.service"

if [[ -f "$PROJECT_SERVICE" ]]; then
    log "deploy/harubot.service 를 환경에 맞춰 설치..."
    # 사용자/경로를 현재 셋업에 맞춰 치환
    sed -E \
        -e "s|^User=.*|User=$HARUBOT_USER|" \
        -e "s|^Group=.*|Group=$HARUBOT_USER|" \
        -e "s|^WorkingDirectory=.*|WorkingDirectory=$HARUBOT_DIR|" \
        -e "s|^ExecStart=.*|ExecStart=$HARUBOT_DIR/.venv/bin/python bot.py|" \
        "$PROJECT_SERVICE" > "$SERVICE_FILE"
else
    log "systemd 유닛 자동 생성..."
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=HaruBot Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$HARUBOT_USER
Group=$HARUBOT_USER
WorkingDirectory=$HARUBOT_DIR
ExecStart=$HARUBOT_DIR/.venv/bin/python bot.py
Restart=always
RestartSec=5
# 자원 보호
LimitNOFILE=4096
# 로그 회전(journald 가 알아서 처리)
StandardOutput=journal
StandardError=journal
# 환경 — .env 는 config.py 가 dotenv 로 직접 읽음
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
fi

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
success "systemd 유닛 '${SERVICE_NAME}.service' 등록 + 자동 부팅 활성화"

# ───────── 7. 자동 시작(선택) ─────────
if [[ "$HARUBOT_AUTO_START" == "1" ]]; then
    if [[ -f "$HARUBOT_DIR/.env" ]]; then
        log "봇 시작..."
        systemctl restart "$SERVICE_NAME"
        sleep 2
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            success "봇이 정상적으로 시작됐습니다."
        else
            warn "봇 시작 실패. 로그를 확인하세요: journalctl -u $SERVICE_NAME -e"
        fi
    else
        warn ".env 가 없어 자동 시작을 건너뜁니다."
    fi
fi

# ───────── 마무리 ─────────
echo
echo "════════════════════════════════════════════════════"
success "셋업 완료!"
echo "════════════════════════════════════════════════════"
echo
echo "다음 단계:"
echo "  1) .env 채우기:    nano $HARUBOT_DIR/.env"
echo "  2) 봇 시작:        sudo systemctl start $SERVICE_NAME"
echo "  3) 라이브 로그:    sudo journalctl -u $SERVICE_NAME -f"
echo "  4) 상태 확인:      sudo systemctl status $SERVICE_NAME"
echo "  5) 중지:           sudo systemctl stop $SERVICE_NAME"
echo "  6) 재시작:         sudo systemctl restart $SERVICE_NAME"
echo
echo "부팅 시 자동 시작: 이미 활성화됨 (enable)"
echo
echo "정상 동작 확인을 위해 봇 시작 후 다음 fingerprint 로그 4개를 확인하세요:"
echo "  - cogs.leveling : Leveling cog 로드 — 메시지 XP 1/60s..."
echo "  - cogs.stats_rpg: StatsRPG cog 로드 — 레벨당 4포인트..."
echo "  - cogs.bump     : Bump cog 로드 — 역할 기반 알림 활성..."
echo "  - cogs.raid     : Raid cog 로드 — Phase 3.5..."
echo
