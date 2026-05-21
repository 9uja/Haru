"""PostgreSQL 데이터 계층 (asyncpg). Neon 등 외부 관리형 Postgres 대상."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id       BIGINT PRIMARY KEY,
    log_channel_id BIGINT
);

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
"""


class Database:
    """asyncpg 연결 풀 래퍼. 음성 활동 기록 및 길드 설정 영속화."""

    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("DB 풀이 초기화되지 않았습니다. connect() 를 먼저 호출하세요.")
        return self._pool

    async def connect(self, dsn: str) -> None:
        # 저사양 호스트 + Neon 무료 연결 한도를 고려해 작은 풀 사용
        self._pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # --- 길드 설정 ---
    async def set_log_channel(self, guild_id: int, channel_id: int) -> None:
        await self.pool.execute(
            """
            INSERT INTO guild_config (guild_id, log_channel_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET log_channel_id = EXCLUDED.log_channel_id
            """,
            guild_id,
            channel_id,
        )

    async def get_log_channel(self, guild_id: int) -> Optional[int]:
        return await self.pool.fetchval(
            "SELECT log_channel_id FROM guild_config WHERE guild_id = $1", guild_id
        )

    # --- 음성 활동 ---
    async def touch_active(self, guild_id: int, user_id: int, when: datetime) -> None:
        """last_active 만 갱신 (입장/하트비트/기동 스캔 시). 과거로 덮어쓰지 않음."""
        await self.pool.execute(
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
        await self.pool.execute(
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
        return await self.pool.fetchrow(
            "SELECT last_active, total_seconds FROM voice_activity WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )

    # --- 서버(길드) 입·퇴장 ---
    async def record_member_join(self, guild_id: int, user_id: int, when: datetime) -> int:
        """서버 입장: join_count +1 + last_joined_at 갱신. 누적 입장 횟수를 반환."""
        return await self.pool.fetchval(
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
        )

    async def record_member_leave(self, guild_id: int, user_id: int, when: datetime) -> int:
        """서버 퇴장: leave_count +1 + last_left_at 갱신. 누적 퇴장 횟수를 반환."""
        return await self.pool.fetchval(
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
        )

    async def get_member_log(self, guild_id: int, user_id: int) -> Optional[asyncpg.Record]:
        """단일 멤버의 서버 입·퇴장 통계."""
        return await self.pool.fetchrow(
            """
            SELECT join_count, leave_count, last_joined_at, last_left_at
            FROM member_log WHERE guild_id = $1 AND user_id = $2
            """,
            guild_id,
            user_id,
        )

    async def get_activity_map(self, guild_id: int) -> dict[int, datetime]:
        """길드 내 user_id -> 마지막 활동시각 매핑. 비활성 판정에 사용."""
        rows = await self.pool.fetch(
            "SELECT user_id, last_active FROM voice_activity WHERE guild_id = $1", guild_id
        )
        return {r["user_id"]: r["last_active"] for r in rows}
