"""환경 변수 로딩 및 설정값 노출."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    token: str
    guild_id: int
    database_url: str
    log_level: str
    inactive_days: int
    report_interval_hours: int
    gemini_api_key: str | None = None
    ai_cooldown_seconds: int = 10
    react_chance: float = 0.05
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    random_reply_chance: float = 0.02
    chat_history_turns: int = 8
    chat_history_max_rows: int = 500000


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"환경 변수 '{name}' 가 설정되지 않았습니다. .env 파일을 확인하세요 (.env.example 참고)."
        )
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    if not raw.lstrip("-").isdigit():
        raise RuntimeError(f"환경 변수 '{name}' 는 정수여야 합니다 (현재: {raw!r}).")
    return int(raw)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"환경 변수 '{name}' 는 숫자여야 합니다 (현재: {raw!r}).")


def load_settings() -> Settings:
    guild_raw = _require("GUILD_ID")
    if not guild_raw.isdigit():
        raise RuntimeError("GUILD_ID 는 숫자로 된 디스코드 서버 ID 여야 합니다.")

    return Settings(
        token=_require("DISCORD_TOKEN"),
        guild_id=int(guild_raw),
        database_url=_require("DATABASE_URL"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        inactive_days=_int_env("INACTIVE_DAYS", 30),
        report_interval_hours=_int_env("REPORT_INTERVAL_HOURS", 168),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        ai_cooldown_seconds=_int_env("AI_COOLDOWN_SECONDS", 10),
        react_chance=_float_env("REACT_CHANCE", 0.05),
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        groq_model=os.getenv("GROQ_MODEL") or "llama-3.3-70b-versatile",
        random_reply_chance=_float_env("REPLY_CHANCE", 0.02),
        chat_history_turns=_int_env("CHAT_HISTORY_TURNS", 8),
        chat_history_max_rows=_int_env("CHAT_HISTORY_MAX_ROWS", 500000),
    )
