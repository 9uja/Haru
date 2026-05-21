# Python 3.12 슬림: audioop 가 표준 라이브러리에 남아 있어 의존성 호환이 가장 안정적
FROM python:3.12-slim

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Koyeb 는 PORT 환경변수를 주입 → keepalive 헬스체크 서버가 해당 포트로 응답
CMD ["python", "bot.py"]
