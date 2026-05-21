#!/usr/bin/env bash
# 무료 봇 호스팅 패널(Wispbyte/HeavenCloud 등)용 시작 스크립트.
# 패널 시작 명령(Startup Command)에 `bash start.sh` 를 지정하면 됩니다.
set -e
pip install --no-cache-dir -r requirements.txt
exec python bot.py
