"""PostgreSQL 데이터 계층 (asyncpg). Neon 등 외부 관리형 Postgres 대상.

Neon 무료 티어는 유휴 시 컴퓨트를 일시정지(autosuspend)하므로, 잠든 직후 첫 쿼리는
콜드 스타트로 연결이 느리거나 끊긴 연결을 잡을 수 있다. 이를 견디기 위해
연결/일시 오류 시 자동 재시도하고, 유휴 연결은 짧게 회수한다.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

import asyncpg

log = logging.getLogger(__name__)

# 콜드 스타트·네트워크 끊김 등 일시적 오류(TimeoutError 는 OSError 하위)
RETRY_ERRORS = (OSError, asyncpg.InterfaceError, asyncpg.PostgresConnectionError)

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id           BIGINT PRIMARY KEY,
    log_channel_id     BIGINT,
    welcome_channel_id BIGINT
);
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS welcome_channel_id BIGINT;

CREATE TABLE IF NOT EXISTS voice_activity (
    guild_id      BIGINT NOT NULL,
    user_id       BIGINT NOT NULL,
    last_active   TIMESTAMPTZ NOT NULL,
    total_seconds BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

-- 서버(길드) 입·퇴장 횟수: 멤버가 서버를 나갔다 다시 들어온 기록
CREATE TABLE IF NOT EXISTS member_log (
    guild_id       BIGINT NOT NULL,
    user_id        BIGINT NOT NULL,
    join_count     BIGINT NOT NULL DEFAULT 0,
    leave_count    BIGINT NOT NULL DEFAULT 0,
    last_joined_at TIMESTAMPTZ,
    last_left_at   TIMESTAMPTZ,
    PRIMARY KEY (guild_id, user_id)
);

-- 유저 경고 기록
CREATE TABLE IF NOT EXISTS warnings (
    id           BIGSERIAL PRIMARY KEY,
    guild_id     BIGINT NOT NULL,
    user_id      BIGINT NOT NULL,
    moderator_id BIGINT,
    reason       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_warnings_guild_user ON warnings (guild_id, user_id);

-- 범프 리마인더: 다음 알림 시각(재시작에도 유지)
CREATE TABLE IF NOT EXISTS bump_reminder (
    guild_id   BIGINT PRIMARY KEY,
    channel_id BIGINT,
    remind_at  TIMESTAMPTZ
);
"""


class Database:
    """asyncpg 연결 풀 래퍼. 연결/일시 오류 시 자동 재시도."""

    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("DB 풀이 초기화되지 않았습니다. connect() 를 먼저 호출하세요.")
        return self._pool

    async def connect(self, dsn: str) -> None:
        # min_size=0: 유휴 시 연결을 잡지 않아 Neon 이 일시정지(무료 컴퓨트 절약)될 수 있게 함.
        # max_inactive_connection_lifetime: 유휴 연결을 일찍 회수해 끊긴 연결 사용을 방지.
        self._pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=0,
            max_size=3,
            command_timeout=15,
            max_inactive_connection_lifetime=30,
        )
        await self._run(lambda con: con.execute(SCHEMA))

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ------------------------------------------------------------ 재시도 래퍼
    async def _run(self, fn, *, retries: int = 3):
        """fn(connection) 을 실행하되 일시적 연결 오류 시 지수 백오프(최대 8초)로 재시도."""
        delay = 1.0
        last_exc: Optional[BaseException] = None
        for attempt in range(retries + 1):
            try:
                async with self.pool.acquire() as con:
                    return await fn(con)
            except RETRY_ERRORS as exc:
                last_exc = exc
                if attempt < retries:
                    log.warning("DB 일시 오류, 재시도 %d/%d: %s", attempt + 1, retries, exc)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 8.0)
        assert last_exc is not None
        raise last_exc

    async def _execute(self, query: str, *args, retries: int = 3) -> None:
        await self._run(lambda con: con.execute(query, *args), retries=retries)

    async def _fetch(self, query: str, *args, retries: int = 3) -> list[asyncpg.Record]:
        return await self._run(lambda con: con.fetch(query, *args), retries=retries)

    async def _fetchrow(self, query: str, *args, retries: int = 3) -> Optional[asyncpg.Record]:
        return await self._run(lambda con: con.fetchrow(query, *args), retries=retries)

    async def _fetchval(self, query: str, *args, retries: int = 3):
        return await self._run(lambda con: con.fetchval(query, *args), retries=retries)

    # ------------------------------------------------------------ 길드 설정
    async def set_log_channel(self, guild_id: int, channel_id: int) -> None:
        await self._execute(
            """
            INSERT INTO guild_config (guild_id, log_channel_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET log_channel_id = EXCLUDED.log_channel_id
            """,
            guild_id,
            channel_id,
        )

    async def get_log_channel(self, guild_id: int) -> Optional[int]:
        return await self._fetchval(
            "SELECT log_channel_id FROM guild_config WHERE guild_id = $1", guild_id
        )

    async def set_welcome_channel(self, guild_id: int, channel_id: int) -> None:
        await self._execute(
            """
            INSERT INTO guild_config (guild_id, welcome_channel_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET welcome_channel_id = EXCLUDED.welcome_channel_id
            """,
            guild_id,
            channel_id,
        )

    async def get_welcome_channel(self, guild_id: int) -> Optional[int]:
        return await self._fetchval(
            "SELECT welcome_channel_id FROM guild_config WHERE guild_id = $1", guild_id
        )

    # ------------------------------------------------------------ 음성 활동
    async def touch_active(self, guild_id: int, user_id: int, when: datetime) -> None:
        """last_active 만 갱신 (입장/하트비트/기동 스캔 시). 과거로 덮어쓰지 않음."""
        await self._execute(
            """
            INSERT INTO voice_activity (guild_id, user_id, last_active)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET last_active = GREATEST(voice_activity.last_active, EXCLUDED.last_active)
            """,
            guild_id,
            user_id,
            when,
        )

    async def add_session(self, guild_id: int, user_id: int, seconds: int, when: datetime) -> None:
        """음성 세션 종료(퇴장) 시 누적 체류시간 합산 + last_active 갱신."""
        await self._execute(
            """
            INSERT INTO voice_activity (guild_id, user_id, last_active, total_seconds)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET last_active   = GREATEST(voice_activity.last_active, EXCLUDED.last_active),
                          total_seconds = voice_activity.total_seconds + EXCLUDED.total_seconds
            """,
            guild_id,
            user_id,
            when,
            max(seconds, 0),
        )

    async def get_voice_stats(self, guild_id: int, user_id: int) -> Optional[asyncpg.Record]:
        """단일 멤버의 음성 통계(마지막 활동, 누적 체류시간)."""
        return await self._fetchrow(
            "SELECT last_active, total_seconds FROM voice_activity WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )

    # ------------------------------------------------------------ 서버(길드) 입·퇴장
    async def record_member_join(self, guild_id: int, user_id: int, when: datetime) -> int:
        """서버 입장: join_count +1 + last_joined_at 갱신. 누적 입장 횟수를 반환.

        1회성 이벤트라 누락 시 영구 손실 → 재시도를 더 길게(5회).
        """
        return await self._fetchval(
            """
            INSERT INTO member_log (guild_id, user_id, join_count, last_joined_at)
            VALUES ($1, $2, 1, $3)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET join_count     = member_log.join_count + 1,
                          last_joined_at = EXCLUDED.last_joined_at
            RETURNING join_count
            """,
            guild_id,
            user_id,
            when,
            retries=5,
        )

    async def record_member_leave(self, guild_id: int, user_id: int, when: datetime) -> int:
        """서버 퇴장: leave_count +1 + last_left_at 갱신. 누적 퇴장 횟수를 반환.

        1회성 이벤트라 누락 시 영구 손실 → 재시도를 더 길게(5회).
        """
        return await self._fetchval(
            """
            INSERT INTO member_log (guild_id, user_id, leave_count, last_left_at)
            VALUES ($1, $2, 1, $3)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET leave_count  = member_log.leave_count + 1,
                          last_left_at = EXCLUDED.last_left_at
            RETURNING leave_count
            """,
            guild_id,
            user_id,
            when,
            retries=5,
        )

    async def get_member_log(self, guild_id: int, user_id: int) -> Optional[asyncpg.Record]:
        """단일 멤버의 서버 입·퇴장 통계."""
        return await self._fetchrow(
            """
            SELECT join_count, leave_count, last_joined_at, last_left_at
            FROM member_log WHERE guild_id = $1 AND user_id = $2
            """,
            guild_id,
            user_id,
        )

    # ------------------------------------------------------------ 경고
    async def add_warning(
        self, guild_id: int, user_id: int, moderator_id: int, reason: str, when: datetime
    ) -> int:
        """경고 추가 후 해당 유저의 누적 경고 수를 반환. (1회성 → 재시도 5회)"""
        await self._execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at)"
            " VALUES ($1, $2, $3, $4, $5)",
            guild_id,
            user_id,
            moderator_id,
            reason,
            when,
            retries=5,
        )
        return await self.get_warning_count(guild_id, user_id)

    async def get_warning_count(self, guild_id: int, user_id: int) -> int:
        return await self._fetchval(
            "SELECT count(*) FROM warnings WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        ) or 0

    async def get_warnings(self, guild_id: int, user_id: int, limit: int = 5) -> list[asyncpg.Record]:
        return await self._fetch(
            "SELECT reason, moderator_id, created_at FROM warnings"
            " WHERE guild_id = $1 AND user_id = $2 ORDER BY created_at DESC LIMIT $3",
            guild_id,
            user_id,
            limit,
        )

    # ------------------------------------------------------------ 범프 리마인더
    async def set_bump_reminder(self, guild_id: int, channel_id: int, remind_at: datetime) -> None:
        await self._execute(
            "INSERT INTO bump_reminder (guild_id, channel_id, remind_at) VALUES ($1, $2, $3)"
            " ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id,"
            " remind_at = EXCLUDED.remind_at",
            guild_id,
            channel_id,
            remind_at,
        )

    async def get_due_bump_reminder(self, guild_id: int) -> Optional[int]:
        """예약 시각이 지난 리마인더가 있으면 채널 ID 반환(없으면 None)."""
        return await self._fetchval(
            "SELECT channel_id FROM bump_reminder"
            " WHERE guild_id = $1 AND remind_at IS NOT NULL AND remind_at <= now()",
            guild_id,
        )

    async def clear_bump_reminder(self, guild_id: int) -> None:
        await self._execute(
            "UPDATE bump_reminder SET remind_at = NULL WHERE guild_id = $1", guild_id
        )

    async def get_activity_map(self, guild_id: int) -> dict[int, datetime]:
        """길드 내 user_id -> 마지막 활동시각 매핑. 비활성 판정에 사용."""
        rows = await self._fetch(
            "SELECT user_id, last_active FROM voice_activity WHERE guild_id = $1", guild_id
        )
        return {r["user_id"]: r["last_active"] for r in rows}
