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
    welcome_channel_id BIGINT,
    level_msg_xp_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
    level_voice_xp_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    level_up_channel_id    BIGINT,
    raid_channel_id        BIGINT
);
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS welcome_channel_id BIGINT;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS level_msg_xp_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS level_voice_xp_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS level_up_channel_id BIGINT;
ALTER TABLE guild_config ADD COLUMN IF NOT EXISTS raid_channel_id BIGINT;

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


-- AI 참고 지식(관리자가 저장 → 답변에 활용)
CREATE TABLE IF NOT EXISTS knowledge (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_knowledge_guild ON knowledge (guild_id);

-- 대화 맥락(재시작 유지). 채널별 + 유저별 조회. 전체는 DB 위험 시에만 오래된 순 정리.
CREATE TABLE IF NOT EXISTS chat_history (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id    BIGINT,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS user_id BIGINT;
CREATE INDEX IF NOT EXISTS idx_chat_history_ch ON chat_history (channel_id, id);
CREATE INDEX IF NOT EXISTS idx_chat_history_user ON chat_history (user_id, id);

-- 유저별 개인 기억(본인 대화에서만 참고). 서버 지식(knowledge)과 분리.
CREATE TABLE IF NOT EXISTS user_memory (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    user_id    BIGINT NOT NULL,
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_memory ON user_memory (guild_id, user_id, id);

-- 오너 전용: 특정 유저에 대한 봇 동작 오버라이드.
--   mode='ignore'  → AI가 해당 유저 메시지를 완전히 무시(트리거/임의답장/기억 모두)
--   mode='instruct'→ 해당 유저와의 대화에 추가 지시를 system 프롬프트로 주입
CREATE TABLE IF NOT EXISTS user_overrides (
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    mode        TEXT NOT NULL,
    instruction TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, user_id)
);

-- 레벨링: 메시지 XP + 음성 XP 누적. 레벨은 누적 XP 로부터 계산(저장 X).
-- last_msg_xp_at: 메시지 XP 쿨다운 판정용(DB 단일 소스 of truth).
CREATE TABLE IF NOT EXISTS user_xp (
    guild_id        BIGINT NOT NULL,
    user_id         BIGINT NOT NULL,
    xp              BIGINT NOT NULL DEFAULT 0,
    last_msg_xp_at  TIMESTAMPTZ,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_user_xp_rank ON user_xp (guild_id, xp DESC);

-- 인벤토리: 유저별 보유 아이템(아이템 정의는 코드 상수, 여기엔 키만 저장)
CREATE TABLE IF NOT EXISTS user_inventory (
    guild_id  BIGINT NOT NULL,
    user_id   BIGINT NOT NULL,
    item_key  TEXT   NOT NULL,
    qty       INT    NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, item_key)
);

-- 활성 부스트: 유저당 1개. 새 사용 시 덮어씀. expires_at 지나면 자동 무효.
CREATE TABLE IF NOT EXISTS active_boosts (
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    multiplier  REAL NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_active_boosts_exp ON active_boosts (expires_at);

-- RPG 스탯: 힘/민첩/지능/행운 누적 포인트. 레벨당 4포인트 자유 분배.
CREATE TABLE IF NOT EXISTS user_stats (
    guild_id BIGINT NOT NULL,
    user_id  BIGINT NOT NULL,
    str_pt   INT NOT NULL DEFAULT 0,
    agi_pt   INT NOT NULL DEFAULT 0,
    int_pt   INT NOT NULL DEFAULT 0,
    luk_pt   INT NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

-- 스탯 분배 이력(LIFO 환불용). 분배 이벤트 1건당 1행(stat + count).
-- 레벨 손실 시 id DESC 순회로 가장 최근 분배부터 차감/삭제하여 정확히 환불.
CREATE TABLE IF NOT EXISTS stat_allocations (
    id         BIGSERIAL PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    user_id    BIGINT NOT NULL,
    stat       TEXT NOT NULL CHECK (stat IN ('str','agi','int','luk')),
    count      INT  NOT NULL CHECK (count > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alloc_user ON stat_allocations (guild_id, user_id, id DESC);

-- 보스 레이드 (Phase 1 MVP)
CREATE TABLE IF NOT EXISTS raids (
    id              BIGSERIAL PRIMARY KEY,
    guild_id        BIGINT NOT NULL,
    boss_key        TEXT NOT NULL,
    max_hp          BIGINT NOT NULL,
    current_hp      BIGINT NOT NULL,
    phase           INT NOT NULL DEFAULT 1,
    status          TEXT NOT NULL,           -- 'active' | 'victory' | 'defeat' | 'cancelled'
    channel_id      BIGINT NOT NULL,
    message_id      BIGINT,                   -- 라이브 임베드 메시지 id
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    last_action_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 길드당 동시 active 1개 보장 (D8 결정)
CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_raid_per_guild
    ON raids (guild_id) WHERE status = 'active';

-- 참가자별 누적 (D13: 데미지 0 = 보상 0 으로 자연 차단)
CREATE TABLE IF NOT EXISTS raid_participants (
    raid_id      BIGINT NOT NULL REFERENCES raids(id) ON DELETE CASCADE,
    user_id      BIGINT NOT NULL,
    total_damage BIGINT NOT NULL DEFAULT 0,
    hits         INT NOT NULL DEFAULT 0,
    skills_used  INT NOT NULL DEFAULT 0,
    final_blow   BOOLEAN NOT NULL DEFAULT FALSE,
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (raid_id, user_id)
);

-- 이미지 CDN URL 캐시 (로컬 파일을 첫 소환에 업로드한 결과 저장 — 이후 edit 에 재사용)
ALTER TABLE raids ADD COLUMN IF NOT EXISTS image_url     TEXT;
ALTER TABLE raids ADD COLUMN IF NOT EXISTS thumbnail_url TEXT;

-- 유저별 학습 스킬 (스킬 해방 시 1행 INSERT, 영구 보존)
CREATE TABLE IF NOT EXISTS user_skills (
    guild_id   BIGINT NOT NULL,
    user_id    BIGINT NOT NULL,
    skill_key  TEXT NOT NULL,
    learned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, user_id, skill_key)
);
CREATE INDEX IF NOT EXISTS idx_user_skills_user ON user_skills (guild_id, user_id);

-- 행동 로그 (라이브 임베드의 "최근 5턴" 표시용)
CREATE TABLE IF NOT EXISTS raid_actions (
    id        BIGSERIAL PRIMARY KEY,
    raid_id   BIGINT NOT NULL REFERENCES raids(id) ON DELETE CASCADE,
    user_id   BIGINT,                          -- 보스 연출 행동은 NULL
    action    TEXT NOT NULL,                    -- 'attack' | 'boss_flavor' | ...
    damage    INT,
    crit      BOOLEAN,
    weakness  BOOLEAN,
    at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_raid_actions_recent
    ON raid_actions (raid_id, id DESC);
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
                    # 자동 복구되는 일시 오류는 소음 방지를 위해 DEBUG (최종 실패는 호출부에서 처리)
                    log.debug("DB 일시 오류, 재시도 %d/%d: %r", attempt + 1, retries, exc)
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

    async def get_member_stats(self, guild_id: int, user_id: int) -> asyncpg.Record:
        """로그 임베드용 통합 스탯(음성·입퇴장·경고)을 한 번에. 없는 값은 NULL."""
        return await self._fetchrow(
            "SELECT "
            "(SELECT total_seconds FROM voice_activity WHERE guild_id=$1 AND user_id=$2) AS total_seconds, "
            "(SELECT last_active FROM voice_activity WHERE guild_id=$1 AND user_id=$2) AS last_active, "
            "(SELECT join_count FROM member_log WHERE guild_id=$1 AND user_id=$2) AS join_count, "
            "(SELECT leave_count FROM member_log WHERE guild_id=$1 AND user_id=$2) AS leave_count, "
            "(SELECT count(*) FROM warnings WHERE guild_id=$1 AND user_id=$2) AS warn_count",
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
    async def set_bump_channel(self, guild_id: int, channel_id: int) -> None:
        """리마인더를 보낼 채널 지정(예약 시각은 보존)."""
        await self._execute(
            "INSERT INTO bump_reminder (guild_id, channel_id) VALUES ($1, $2)"
            " ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id",
            guild_id,
            channel_id,
        )

    async def get_bump_state(self, guild_id: int) -> Optional[asyncpg.Record]:
        """리마인더 채널·예약 시각(시작 시 메모리 캐시로 로드)."""
        return await self._fetchrow(
            "SELECT channel_id, remind_at FROM bump_reminder WHERE guild_id = $1", guild_id
        )

    async def schedule_bump_reminder(self, guild_id: int, remind_at: datetime) -> None:
        """채널이 지정된 경우에만 예약 시각 갱신(미지정이면 0행 → 무시)."""
        await self._execute(
            "UPDATE bump_reminder SET remind_at = $2 WHERE guild_id = $1", guild_id, remind_at
        )

    async def clear_bump_reminder(self, guild_id: int) -> None:
        await self._execute(
            "UPDATE bump_reminder SET remind_at = NULL WHERE guild_id = $1", guild_id
        )

    # ------------------------------------------------------------ AI 지식
    async def add_knowledge(self, guild_id: int, content: str) -> int:
        return await self._fetchval(
            "INSERT INTO knowledge (guild_id, content) VALUES ($1, $2) RETURNING id",
            guild_id,
            content,
            retries=5,
        )

    async def list_knowledge(self, guild_id: int, limit: int = 50) -> list[asyncpg.Record]:
        return await self._fetch(
            "SELECT id, content FROM knowledge WHERE guild_id = $1 ORDER BY id LIMIT $2",
            guild_id,
            limit,
        )

    async def delete_knowledge(self, guild_id: int, knowledge_id: int) -> bool:
        deleted = await self._fetchval(
            "DELETE FROM knowledge WHERE guild_id = $1 AND id = $2 RETURNING id",
            guild_id,
            knowledge_id,
        )
        return deleted is not None

    # ------------------------------------------------------------ 대화 맥락
    async def get_context(
        self, guild_id: int, channel_id: int, user_id: int, limit: int
    ) -> list[asyncpg.Record]:
        """이 채널의 최근 대화 + 이 유저의 최근 대화(다른 채널 포함)를 합쳐 최근순 limit개, 오래된 순 반환."""
        rows = await self._fetch(
            "SELECT role, content FROM chat_history "
            "WHERE channel_id = $2 OR (guild_id = $1 AND user_id = $3) "
            "ORDER BY id DESC LIMIT $4",
            guild_id,
            channel_id,
            user_id,
            limit,
        )
        return list(reversed(rows))

    async def add_chat_turns(
        self, guild_id: int, channel_id: int, user_id: int, turns: list[tuple[str, str]]
    ) -> None:
        """대화 턴 저장(정리는 별도 유지보수 루프에서 DB 위험 시에만)."""
        await self._run(
            lambda con: con.executemany(
                "INSERT INTO chat_history (guild_id, channel_id, user_id, role, content)"
                " VALUES ($1, $2, $3, $4, $5)",
                [(guild_id, channel_id, user_id, role, content[:300]) for role, content in turns],
            ),
            retries=3,
        )

    async def count_chat_history(self) -> int:
        return await self._fetchval("SELECT count(*) FROM chat_history") or 0

    async def prune_chat_history(self, keep: int) -> int:
        """오래된 순으로 삭제하고 최신 keep개만 유지. 삭제 행 수 반환."""
        return await self._fetchval(
            "WITH del AS (DELETE FROM chat_history WHERE id NOT IN "
            "(SELECT id FROM chat_history ORDER BY id DESC LIMIT $1) RETURNING 1) "
            "SELECT count(*) FROM del",
            keep,
        ) or 0

    # ------------------------------------------------------------ 유저 개인 기억
    async def add_user_memory(self, guild_id: int, user_id: int, content: str) -> int:
        return await self._fetchval(
            "INSERT INTO user_memory (guild_id, user_id, content) VALUES ($1, $2, $3) RETURNING id",
            guild_id,
            user_id,
            content,
            retries=5,
        )

    async def list_user_memory(self, guild_id: int, user_id: int, limit: int = 50) -> list[asyncpg.Record]:
        return await self._fetch(
            "SELECT id, content FROM user_memory WHERE guild_id = $1 AND user_id = $2 ORDER BY id LIMIT $3",
            guild_id,
            user_id,
            limit,
        )

    async def delete_user_memory(self, guild_id: int, user_id: int, memory_id: int) -> bool:
        deleted = await self._fetchval(
            "DELETE FROM user_memory WHERE guild_id = $1 AND user_id = $2 AND id = $3 RETURNING id",
            guild_id,
            user_id,
            memory_id,
        )
        return deleted is not None

    async def get_user_memory_context(self, guild_id: int, user_id: int, budget: int = 800) -> str:
        rows = await self._fetch(
            "SELECT content FROM user_memory WHERE guild_id = $1 AND user_id = $2 ORDER BY id DESC",
            guild_id,
            user_id,
        )
        out: list[str] = []
        total = 0
        for r in rows:
            line = "- " + r["content"]
            if total + len(line) + 1 > budget:
                break
            out.append(line)
            total += len(line) + 1
        return "\n".join(out)

    async def get_knowledge_context(self, guild_id: int, budget: int = 1500) -> str:
        """최근 지식부터 예산(문자 수) 안에서 합쳐 반환."""
        rows = await self._fetch(
            "SELECT content FROM knowledge WHERE guild_id = $1 ORDER BY id DESC", guild_id
        )
        out: list[str] = []
        total = 0
        for r in rows:
            line = "- " + r["content"]
            if total + len(line) + 1 > budget:
                break
            out.append(line)
            total += len(line) + 1
        return "\n".join(out)

    async def get_activity_map(self, guild_id: int) -> dict[int, datetime]:
        """길드 내 user_id -> 마지막 활동시각 매핑. 비활성 판정에 사용."""
        rows = await self._fetch(
            "SELECT user_id, last_active FROM voice_activity WHERE guild_id = $1", guild_id
        )
        return {r["user_id"]: r["last_active"] for r in rows}

    # ------------------------------------------------------------ 오너 전용: 유저 오버라이드
    async def set_user_override(
        self, guild_id: int, user_id: int, mode: str, instruction: Optional[str] = None
    ) -> None:
        """유저 동작 오버라이드 설정/갱신. mode='ignore' 또는 'instruct'."""
        await self._execute(
            "INSERT INTO user_overrides (guild_id, user_id, mode, instruction)"
            " VALUES ($1, $2, $3, $4)"
            " ON CONFLICT (guild_id, user_id)"
            " DO UPDATE SET mode = EXCLUDED.mode, instruction = EXCLUDED.instruction,"
            "               created_at = now()",
            guild_id,
            user_id,
            mode,
            instruction,
            retries=5,
        )

    async def clear_user_override(self, guild_id: int, user_id: int) -> bool:
        """오버라이드 제거. 실제로 삭제됐는지 반환."""
        deleted = await self._fetchval(
            "DELETE FROM user_overrides WHERE guild_id = $1 AND user_id = $2 RETURNING 1",
            guild_id,
            user_id,
            retries=5,
        )
        return deleted is not None

    async def get_user_override(self, guild_id: int, user_id: int) -> Optional[asyncpg.Record]:
        """특정 유저의 오버라이드(없으면 None)."""
        return await self._fetchrow(
            "SELECT mode, instruction FROM user_overrides WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )

    async def list_user_overrides(self, guild_id: int) -> list[asyncpg.Record]:
        return await self._fetch(
            "SELECT user_id, mode, instruction FROM user_overrides"
            " WHERE guild_id = $1 ORDER BY created_at DESC",
            guild_id,
        )

    # ------------------------------------------------------------ 오너 전용: 영역별 DB 초기화
    # 각 메서드는 해당 영역 테이블을 TRUNCATE (스키마 보존). 위험하므로 코그 단계에서 권한·확인 게이트.
    async def reset_chat_history(self, guild_id: int) -> int:
        return await self._fetchval(
            "WITH d AS (DELETE FROM chat_history WHERE guild_id = $1 RETURNING 1)"
            " SELECT count(*) FROM d",
            guild_id,
            retries=5,
        ) or 0

    async def reset_memory(self, guild_id: int) -> int:
        """knowledge + user_memory 전체 삭제(길드 한정). 삭제 총 행 수 반환."""
        deleted = await self._fetchval(
            "WITH a AS (DELETE FROM knowledge WHERE guild_id = $1 RETURNING 1),"
            "     b AS (DELETE FROM user_memory WHERE guild_id = $1 RETURNING 1)"
            " SELECT (SELECT count(*) FROM a) + (SELECT count(*) FROM b)",
            guild_id,
            retries=5,
        )
        return int(deleted or 0)

    async def reset_voice(self, guild_id: int) -> int:
        deleted = await self._fetchval(
            "WITH a AS (DELETE FROM voice_activity WHERE guild_id = $1 RETURNING 1),"
            "     b AS (DELETE FROM member_log WHERE guild_id = $1 RETURNING 1)"
            " SELECT (SELECT count(*) FROM a) + (SELECT count(*) FROM b)",
            guild_id,
            retries=5,
        )
        return int(deleted or 0)

    async def reset_warnings(self, guild_id: int) -> int:
        return await self._fetchval(
            "WITH d AS (DELETE FROM warnings WHERE guild_id = $1 RETURNING 1)"
            " SELECT count(*) FROM d",
            guild_id,
            retries=5,
        ) or 0

    # ------------------------------------------------------------ 레벨링(XP)
    async def add_message_xp(
        self, guild_id: int, user_id: int, amount: int, when: datetime
    ) -> tuple[int, int]:
        """메시지 XP 적립. 쿨다운(60초) 체크는 호출 측에서 last_msg_xp_at 비교로 처리.
        반환: (이전 누적 xp, 증가 후 누적 xp).
        """
        before = await self._fetchval(
            "SELECT xp FROM user_xp WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        ) or 0
        after = await self._fetchval(
            """
            INSERT INTO user_xp (guild_id, user_id, xp, last_msg_xp_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, user_id) DO UPDATE
              SET xp = user_xp.xp + EXCLUDED.xp,
                  last_msg_xp_at = EXCLUDED.last_msg_xp_at
            RETURNING xp
            """,
            guild_id, user_id, max(amount, 0), when,
            retries=5,
        )
        return int(before), int(after or 0)

    async def add_voice_xp(
        self, guild_id: int, user_id: int, amount: int
    ) -> tuple[int, int]:
        """음성 XP 적립(쿨다운 없음). last_msg_xp_at 은 건드리지 않는다.
        반환: (이전, 이후) 누적 xp.
        """
        before = await self._fetchval(
            "SELECT xp FROM user_xp WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        ) or 0
        after = await self._fetchval(
            """
            INSERT INTO user_xp (guild_id, user_id, xp)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, user_id) DO UPDATE
              SET xp = user_xp.xp + EXCLUDED.xp
            RETURNING xp
            """,
            guild_id, user_id, max(amount, 0),
            retries=5,
        )
        return int(before), int(after or 0)

    async def get_msg_xp_cooldown(self, guild_id: int, user_id: int) -> Optional[datetime]:
        """해당 유저의 마지막 메시지 XP 적립 시각(없으면 None)."""
        return await self._fetchval(
            "SELECT last_msg_xp_at FROM user_xp WHERE guild_id = $1 AND user_id = $2",
            guild_id, user_id,
        )

    async def get_user_xp(self, guild_id: int, user_id: int) -> int:
        return int(await self._fetchval(
            "SELECT xp FROM user_xp WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        ) or 0)

    async def get_user_rank(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """유저의 (순위, 길드 전체 인원수). XP 동률은 user_id 가 작은 쪽이 위."""
        row = await self._fetchrow(
            """
            WITH ranked AS (
              SELECT user_id, RANK() OVER (ORDER BY xp DESC, user_id ASC) AS rnk
              FROM user_xp WHERE guild_id = $1
            )
            SELECT (SELECT rnk FROM ranked WHERE user_id = $2) AS rnk,
                   (SELECT count(*) FROM user_xp WHERE guild_id = $1) AS total
            """,
            guild_id, user_id,
        )
        if row is None or row["rnk"] is None:
            total = int(row["total"]) if row else 0
            return 0, total
        return int(row["rnk"]), int(row["total"])

    async def top_xp(self, guild_id: int, limit: int = 10) -> list[asyncpg.Record]:
        return await self._fetch(
            "SELECT user_id, xp FROM user_xp WHERE guild_id = $1"
            " ORDER BY xp DESC, user_id ASC LIMIT $2",
            guild_id, limit,
        )

    # ------------------------------------------------------------ 레벨링: 설정
    async def get_level_config(self, guild_id: int) -> asyncpg.Record:
        """(msg_xp_enabled, voice_xp_enabled, level_up_channel_id). 없으면 기본 True/True/None."""
        row = await self._fetchrow(
            "SELECT level_msg_xp_enabled AS msg, level_voice_xp_enabled AS voice,"
            " level_up_channel_id AS ch FROM guild_config WHERE guild_id = $1",
            guild_id,
        )
        return row  # caller가 None 처리

    async def set_level_config(
        self,
        guild_id: int,
        *,
        msg_xp_enabled: Optional[bool] = None,
        voice_xp_enabled: Optional[bool] = None,
        level_up_channel_id: Optional[int] = None,
        clear_channel: bool = False,
    ) -> None:
        """부분 갱신. 명시되지 않은 필드는 그대로 유지. clear_channel=True 면 알림채널 NULL 로."""
        # 먼저 ROW 가 있는지 보장
        await self._execute(
            "INSERT INTO guild_config (guild_id) VALUES ($1)"
            " ON CONFLICT (guild_id) DO NOTHING",
            guild_id,
        )
        sets: list[str] = []
        args: list = [guild_id]
        if msg_xp_enabled is not None:
            args.append(msg_xp_enabled)
            sets.append(f"level_msg_xp_enabled = ${len(args)}")
        if voice_xp_enabled is not None:
            args.append(voice_xp_enabled)
            sets.append(f"level_voice_xp_enabled = ${len(args)}")
        if clear_channel:
            sets.append("level_up_channel_id = NULL")
        elif level_up_channel_id is not None:
            args.append(level_up_channel_id)
            sets.append(f"level_up_channel_id = ${len(args)}")
        if not sets:
            return
        await self._execute(
            f"UPDATE guild_config SET {', '.join(sets)} WHERE guild_id = $1", *args, retries=5,
        )

    # ------------------------------------------------------------ 레벨링: 인벤토리
    async def add_to_inventory(
        self, guild_id: int, user_id: int, item_key: str, qty: int = 1
    ) -> int:
        """수량 증가, 새 누적 qty 반환."""
        return await self._fetchval(
            "INSERT INTO user_inventory (guild_id, user_id, item_key, qty)"
            " VALUES ($1, $2, $3, $4)"
            " ON CONFLICT (guild_id, user_id, item_key) DO UPDATE"
            "   SET qty = user_inventory.qty + EXCLUDED.qty"
            " RETURNING qty",
            guild_id, user_id, item_key, max(qty, 0),
            retries=5,
        ) or 0

    async def list_inventory(
        self, guild_id: int, user_id: int
    ) -> list[asyncpg.Record]:
        return await self._fetch(
            "SELECT item_key, qty FROM user_inventory"
            " WHERE guild_id = $1 AND user_id = $2 AND qty > 0"
            " ORDER BY item_key",
            guild_id, user_id,
        )

    async def consume_item(
        self, guild_id: int, user_id: int, item_key: str
    ) -> bool:
        """qty 1 차감. 차감 성공 시 True. 보유가 없거나 0이면 False."""
        row = await self._fetchval(
            "UPDATE user_inventory SET qty = qty - 1"
            " WHERE guild_id = $1 AND user_id = $2 AND item_key = $3 AND qty > 0"
            " RETURNING qty",
            guild_id, user_id, item_key, retries=5,
        )
        return row is not None

    # ------------------------------------------------------------ 레벨링: 부스트
    async def set_active_boost(
        self, guild_id: int, user_id: int, multiplier: float, expires_at: datetime
    ) -> None:
        """현재 부스트를 새 것으로 교체(덮어쓰기)."""
        await self._execute(
            "INSERT INTO active_boosts (guild_id, user_id, multiplier, expires_at)"
            " VALUES ($1, $2, $3, $4)"
            " ON CONFLICT (guild_id, user_id) DO UPDATE"
            "   SET multiplier = EXCLUDED.multiplier, expires_at = EXCLUDED.expires_at",
            guild_id, user_id, multiplier, expires_at, retries=5,
        )

    async def get_active_boost(
        self, guild_id: int, user_id: int
    ) -> Optional[asyncpg.Record]:
        """만료되지 않은 부스트만 반환(만료된 행은 무시)."""
        return await self._fetchrow(
            "SELECT multiplier, expires_at FROM active_boosts"
            " WHERE guild_id = $1 AND user_id = $2 AND expires_at > now()",
            guild_id, user_id,
        )

    async def clear_expired_boosts(self) -> int:
        """만료 부스트 일괄 삭제(유지보수). 삭제 행 수 반환."""
        return await self._fetchval(
            "WITH d AS (DELETE FROM active_boosts WHERE expires_at <= now() RETURNING 1)"
            " SELECT count(*) FROM d"
        ) or 0

    async def reset_levels(self, guild_id: int) -> int:
        """user_xp + 인벤토리 + 부스트 + 스탯 + 분배이력 + 학습스킬 모두 비움."""
        deleted = await self._fetchval(
            "WITH a AS (DELETE FROM user_xp WHERE guild_id = $1 RETURNING 1),"
            "     b AS (DELETE FROM user_inventory WHERE guild_id = $1 RETURNING 1),"
            "     c AS (DELETE FROM active_boosts WHERE guild_id = $1 RETURNING 1),"
            "     d AS (DELETE FROM user_stats WHERE guild_id = $1 RETURNING 1),"
            "     e AS (DELETE FROM stat_allocations WHERE guild_id = $1 RETURNING 1),"
            "     f AS (DELETE FROM user_skills WHERE guild_id = $1 RETURNING 1)"
            " SELECT (SELECT count(*) FROM a)+(SELECT count(*) FROM b)+(SELECT count(*) FROM c)"
            "       +(SELECT count(*) FROM d)+(SELECT count(*) FROM e)+(SELECT count(*) FROM f)",
            guild_id, retries=5,
        )
        return int(deleted or 0)

    # ------------------------------------------------------------ XP 직접 조작(도박/감소 등)
    async def set_user_xp(self, guild_id: int, user_id: int, xp: int) -> None:
        """절대값 XP 갱신(없으면 0 부터). 호출 측이 환불·레벨 변화 계산 책임."""
        await self._execute(
            "INSERT INTO user_xp (guild_id, user_id, xp) VALUES ($1, $2, $3)"
            " ON CONFLICT (guild_id, user_id) DO UPDATE SET xp = EXCLUDED.xp",
            guild_id, user_id, max(int(xp), 0),
            retries=5,
        )

    async def subtract_xp(
        self, guild_id: int, user_id: int, amount: int
    ) -> tuple[int, int]:
        """XP 차감(0 floor). (before, after) 반환. amount<=0 이면 변화 없음."""
        before = int(await self._fetchval(
            "SELECT xp FROM user_xp WHERE guild_id = $1 AND user_id = $2",
            guild_id, user_id,
        ) or 0)
        if amount <= 0:
            return before, before
        after = max(0, before - int(amount))
        await self.set_user_xp(guild_id, user_id, after)
        return before, after

    # ------------------------------------------------------------ RPG 스탯
    async def get_user_stats(self, guild_id: int, user_id: int) -> Optional[asyncpg.Record]:
        """(str_pt, agi_pt, int_pt, luk_pt). 행 없으면 None."""
        return await self._fetchrow(
            "SELECT str_pt, agi_pt, int_pt, luk_pt FROM user_stats"
            " WHERE guild_id = $1 AND user_id = $2",
            guild_id, user_id,
        )

    async def get_stats_total(self, guild_id: int, user_id: int) -> int:
        """누적 분배된 포인트 총합(미분배 계산용)."""
        return int(await self._fetchval(
            "SELECT COALESCE(str_pt + agi_pt + int_pt + luk_pt, 0)"
            " FROM user_stats WHERE guild_id = $1 AND user_id = $2",
            guild_id, user_id,
        ) or 0)

    async def allocate_stat(
        self, guild_id: int, user_id: int, stat: str, count: int
    ) -> asyncpg.Record:
        """count 포인트를 stat 에 분배. 호출 측에서 미분배 포인트 보유 검증.
        - user_stats 행 보장 → 해당 컬럼 += count
        - stat_allocations 에 1행 추가 (환불용 LIFO)
        반환: 갱신된 user_stats 행.
        """
        assert stat in ("str", "agi", "int", "luk"), f"unknown stat: {stat}"
        assert count > 0, f"count must be positive, got {count}"
        col = f"{stat}_pt"
        async def _txn(con):
            await con.execute(
                "INSERT INTO user_stats (guild_id, user_id) VALUES ($1, $2)"
                " ON CONFLICT DO NOTHING",
                guild_id, user_id,
            )
            await con.execute(
                f"UPDATE user_stats SET {col} = {col} + $3"
                " WHERE guild_id = $1 AND user_id = $2",
                guild_id, user_id, count,
            )
            await con.execute(
                "INSERT INTO stat_allocations (guild_id, user_id, stat, count)"
                " VALUES ($1, $2, $3, $4)",
                guild_id, user_id, stat, count,
            )
            return await con.fetchrow(
                "SELECT str_pt, agi_pt, int_pt, luk_pt FROM user_stats"
                " WHERE guild_id = $1 AND user_id = $2",
                guild_id, user_id,
            )
        return await self._run(_txn, retries=5)

    async def refund_stat_points(
        self, guild_id: int, user_id: int, points: int
    ) -> dict[str, int]:
        """가장 최근 분배부터 LIFO 로 `points` 만큼 환불.
        - stat_allocations 최신 행을 꺼내서 count 만큼 (또는 부족하면 전체) 환불
        - 행이 부족하면 가능한 만큼만 환불
        반환: {'str':n, 'agi':n, 'int':n, 'luk':n} (환불된 양).
        """
        refunded = {"str": 0, "agi": 0, "int": 0, "luk": 0}
        if points <= 0:
            return refunded

        async def _txn(con):
            remaining = points
            while remaining > 0:
                row = await con.fetchrow(
                    "SELECT id, stat, count FROM stat_allocations"
                    " WHERE guild_id = $1 AND user_id = $2"
                    " ORDER BY id DESC LIMIT 1",
                    guild_id, user_id,
                )
                if row is None:
                    break
                avail = int(row["count"])
                take = min(avail, remaining)
                refunded[row["stat"]] += take
                if take >= avail:
                    await con.execute(
                        "DELETE FROM stat_allocations WHERE id = $1", row["id"]
                    )
                else:
                    await con.execute(
                        "UPDATE stat_allocations SET count = count - $2 WHERE id = $1",
                        row["id"], take,
                    )
                remaining -= take
            # user_stats 컬럼 차감 (음수 방지)
            if any(refunded.values()):
                await con.execute(
                    "UPDATE user_stats SET"
                    "   str_pt = GREATEST(0, str_pt - $3),"
                    "   agi_pt = GREATEST(0, agi_pt - $4),"
                    "   int_pt = GREATEST(0, int_pt - $5),"
                    "   luk_pt = GREATEST(0, luk_pt - $6)"
                    " WHERE guild_id = $1 AND user_id = $2",
                    guild_id, user_id,
                    refunded["str"], refunded["agi"], refunded["int"], refunded["luk"],
                )
            return refunded
        return await self._run(_txn, retries=5)

    # ------------------------------------------------------------ 학습 스킬
    async def learn_skill(
        self, guild_id: int, user_id: int, skill_key: str
    ) -> bool:
        """스킬 1개 학습 등록. 처음 학습이면 True, 이미 있었으면 False."""
        ok = await self._fetchval(
            "INSERT INTO user_skills (guild_id, user_id, skill_key) VALUES ($1, $2, $3)"
            " ON CONFLICT DO NOTHING RETURNING 1",
            guild_id, user_id, skill_key, retries=5,
        )
        return ok is not None

    async def get_learned_skills(
        self, guild_id: int, user_id: int
    ) -> set[str]:
        rows = await self._fetch(
            "SELECT skill_key FROM user_skills WHERE guild_id = $1 AND user_id = $2",
            guild_id, user_id,
        )
        return {r["skill_key"] for r in rows}

    async def reset_user_stats(self, guild_id: int, user_id: int) -> int:
        """해당 유저의 스탯·이력 전부 삭제. 이후 미분배 포인트는 = level*4 가 됨."""
        async def _txn(con):
            n = await con.fetchval(
                "WITH a AS (DELETE FROM user_stats WHERE guild_id = $1 AND user_id = $2 RETURNING 1),"
                "     b AS (DELETE FROM stat_allocations WHERE guild_id = $1 AND user_id = $2 RETURNING 1)"
                " SELECT (SELECT count(*) FROM a)+(SELECT count(*) FROM b)",
                guild_id, user_id,
            )
            return int(n or 0)
        return await self._run(_txn, retries=5)

    # ------------------------------------------------------------ 레이드 채널 설정
    async def set_raid_channel(self, guild_id: int, channel_id: int) -> None:
        await self._execute(
            "INSERT INTO guild_config (guild_id, raid_channel_id) VALUES ($1, $2)"
            " ON CONFLICT (guild_id) DO UPDATE SET raid_channel_id = EXCLUDED.raid_channel_id",
            guild_id, channel_id,
        )

    async def get_raid_channel(self, guild_id: int) -> Optional[int]:
        return await self._fetchval(
            "SELECT raid_channel_id FROM guild_config WHERE guild_id = $1",
            guild_id,
        )

    # ------------------------------------------------------------ 레이드 메타
    async def create_raid(
        self, guild_id: int, boss_key: str, max_hp: int, channel_id: int
    ) -> int:
        """active 레이드 생성. 길드당 1개 unique 인덱스가 동시 생성 차단."""
        return await self._fetchval(
            "INSERT INTO raids (guild_id, boss_key, max_hp, current_hp, status, channel_id)"
            " VALUES ($1, $2, $3, $3, 'active', $4) RETURNING id",
            guild_id, boss_key, max_hp, channel_id,
            retries=3,
        )

    async def get_active_raid(self, guild_id: int) -> Optional[asyncpg.Record]:
        return await self._fetchrow(
            "SELECT id, guild_id, boss_key, max_hp, current_hp, phase, status,"
            " channel_id, message_id, started_at, ended_at, last_action_at,"
            " image_url, thumbnail_url"
            " FROM raids WHERE guild_id = $1 AND status = 'active'",
            guild_id,
        )

    async def get_raid(self, raid_id: int) -> Optional[asyncpg.Record]:
        return await self._fetchrow(
            "SELECT id, guild_id, boss_key, max_hp, current_hp, phase, status,"
            " channel_id, message_id, started_at, ended_at, last_action_at,"
            " image_url, thumbnail_url"
            " FROM raids WHERE id = $1",
            raid_id,
        )

    async def set_raid_message_id(self, raid_id: int, message_id: int) -> None:
        await self._execute(
            "UPDATE raids SET message_id = $2 WHERE id = $1",
            raid_id, message_id, retries=5,
        )

    async def set_raid_phase(self, raid_id: int, phase: int) -> None:
        await self._execute(
            "UPDATE raids SET phase = $2 WHERE id = $1",
            raid_id, int(phase), retries=5,
        )

    async def set_raid_image_urls(
        self, raid_id: int,
        image_url: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
    ) -> None:
        """업로드 결과로 받은 CDN URL 을 저장. None 인쪽은 그대로 유지."""
        if image_url is None and thumbnail_url is None:
            return
        sets: list[str] = []
        args: list = [raid_id]
        if image_url is not None:
            args.append(image_url)
            sets.append(f"image_url = ${len(args)}")
        if thumbnail_url is not None:
            args.append(thumbnail_url)
            sets.append(f"thumbnail_url = ${len(args)}")
        await self._execute(
            f"UPDATE raids SET {', '.join(sets)} WHERE id = $1", *args, retries=5,
        )

    async def apply_raid_heal(
        self, raid_id: int, amount: int
    ) -> tuple[int, int]:
        """current_hp += amount (max_hp cap). 반환 (before, after).
        active 가 아니거나 amount<=0 이면 (0, 0).
        """
        if amount <= 0:
            return 0, 0
        row = await self._fetchrow(
            "WITH prev AS (SELECT current_hp FROM raids WHERE id = $1)"
            " UPDATE raids SET current_hp = LEAST(max_hp, current_hp + $2)"
            "  WHERE id = $1 AND status = 'active' AND current_hp > 0"
            "  RETURNING (SELECT current_hp FROM prev) AS before, current_hp AS after",
            raid_id, int(amount), retries=5,
        )
        if row is None:
            return 0, 0
        return int(row["before"]), int(row["after"])

    async def apply_raid_damage(
        self, raid_id: int, damage: int
    ) -> tuple[int, int]:
        """current_hp -= damage (0 floor). last_action_at 갱신.
        반환: (before_hp, after_hp). after_hp <= 0 이면 호출 측이 종료 처리.
        """
        row = await self._fetchrow(
            "UPDATE raids SET current_hp = GREATEST(0, current_hp - $2),"
            "                 last_action_at = now()"
            " WHERE id = $1 AND status = 'active'"
            " RETURNING (current_hp + $2) AS before_hp, current_hp AS after_hp",
            raid_id, max(0, int(damage)),
            retries=5,
        )
        if row is None:
            return 0, 0
        return int(row["before_hp"]), int(row["after_hp"])

    async def end_raid(
        self, raid_id: int, status: str, final_blow_user_id: Optional[int] = None
    ) -> None:
        """레이드 종료. final_blow 유저가 있으면 표시. status: victory/defeat/cancelled."""
        async def _txn(con):
            await con.execute(
                "UPDATE raids SET status = $2, ended_at = now() WHERE id = $1 AND status = 'active'",
                raid_id, status,
            )
            if final_blow_user_id is not None:
                await con.execute(
                    "UPDATE raid_participants SET final_blow = TRUE"
                    " WHERE raid_id = $1 AND user_id = $2",
                    raid_id, final_blow_user_id,
                )
        await self._run(_txn, retries=5)

    # ------------------------------------------------------------ 참가자
    async def join_raid(self, raid_id: int, user_id: int) -> None:
        """참가자 행 보장(없으면 INSERT, 있으면 noop)."""
        await self._execute(
            "INSERT INTO raid_participants (raid_id, user_id) VALUES ($1, $2)"
            " ON CONFLICT DO NOTHING",
            raid_id, user_id, retries=3,
        )

    async def add_participant_damage(
        self, raid_id: int, user_id: int, damage: int, is_skill: bool = False
    ) -> None:
        """누적 데미지·타격수·스킬사용수 증가. 행이 없으면 생성."""
        await self._execute(
            "INSERT INTO raid_participants (raid_id, user_id, total_damage, hits, skills_used)"
            " VALUES ($1, $2, $3, $4, $5)"
            " ON CONFLICT (raid_id, user_id) DO UPDATE SET"
            "   total_damage = raid_participants.total_damage + EXCLUDED.total_damage,"
            "   hits         = raid_participants.hits         + EXCLUDED.hits,"
            "   skills_used  = raid_participants.skills_used  + EXCLUDED.skills_used",
            raid_id, user_id, max(0, int(damage)),
            0 if is_skill else 1, 1 if is_skill else 0,
            retries=5,
        )

    async def get_raid_participants(
        self, raid_id: int
    ) -> list[asyncpg.Record]:
        """데미지 내림차순, 동률 시 user_id 오름차순."""
        return await self._fetch(
            "SELECT user_id, total_damage, hits, skills_used, final_blow"
            " FROM raid_participants WHERE raid_id = $1"
            " ORDER BY total_damage DESC, user_id ASC",
            raid_id,
        )

    async def get_raid_top_n(
        self, raid_id: int, n: int = 4
    ) -> list[asyncpg.Record]:
        return await self._fetch(
            "SELECT user_id, total_damage FROM raid_participants"
            " WHERE raid_id = $1 ORDER BY total_damage DESC, user_id ASC LIMIT $2",
            raid_id, n,
        )

    async def count_raid_participants(self, raid_id: int) -> int:
        return int(await self._fetchval(
            "SELECT count(*) FROM raid_participants WHERE raid_id = $1",
            raid_id,
        ) or 0)

    # ------------------------------------------------------------ 행동 로그
    async def log_raid_action(
        self, raid_id: int, user_id: Optional[int], action: str,
        damage: Optional[int] = None, crit: bool = False, weakness: bool = False,
    ) -> None:
        await self._execute(
            "INSERT INTO raid_actions (raid_id, user_id, action, damage, crit, weakness)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            raid_id, user_id, action, damage, crit, weakness,
            retries=3,
        )

    async def recent_raid_actions(
        self, raid_id: int, limit: int = 5
    ) -> list[asyncpg.Record]:
        return await self._fetch(
            "SELECT user_id, action, damage, crit, weakness FROM raid_actions"
            " WHERE raid_id = $1 ORDER BY id DESC LIMIT $2",
            raid_id, limit,
        )

    async def reset_all(self, guild_id: int) -> int:
        """길드의 모든 사용자 데이터 삭제. guild_config/bump_reminder.channel_id 는 보존하되 예약은 비움.
        user_overrides·user_xp 도 같이 비운다. 삭제 총 행 수 반환."""
        deleted = await self._fetchval(
            "WITH "
            " a AS (DELETE FROM chat_history WHERE guild_id = $1 RETURNING 1),"
            " b AS (DELETE FROM knowledge WHERE guild_id = $1 RETURNING 1),"
            " c AS (DELETE FROM user_memory WHERE guild_id = $1 RETURNING 1),"
            " d AS (DELETE FROM voice_activity WHERE guild_id = $1 RETURNING 1),"
            " e AS (DELETE FROM member_log WHERE guild_id = $1 RETURNING 1),"
            " f AS (DELETE FROM warnings WHERE guild_id = $1 RETURNING 1),"
            " g AS (DELETE FROM user_overrides WHERE guild_id = $1 RETURNING 1),"
            " h AS (UPDATE bump_reminder SET remind_at = NULL WHERE guild_id = $1 RETURNING 1),"
            " i AS (DELETE FROM user_xp WHERE guild_id = $1 RETURNING 1),"
            " j AS (DELETE FROM user_inventory WHERE guild_id = $1 RETURNING 1),"
            " k AS (DELETE FROM active_boosts WHERE guild_id = $1 RETURNING 1),"
            " l AS (DELETE FROM user_stats WHERE guild_id = $1 RETURNING 1),"
            " m AS (DELETE FROM stat_allocations WHERE guild_id = $1 RETURNING 1),"
            " n AS (DELETE FROM raids WHERE guild_id = $1 RETURNING 1)"
            " SELECT (SELECT count(*) FROM a)+(SELECT count(*) FROM b)+(SELECT count(*) FROM c)"
            "       +(SELECT count(*) FROM d)+(SELECT count(*) FROM e)+(SELECT count(*) FROM f)"
            "       +(SELECT count(*) FROM g)+(SELECT count(*) FROM h)"
            "       +(SELECT count(*) FROM i)+(SELECT count(*) FROM j)+(SELECT count(*) FROM k)"
            "       +(SELECT count(*) FROM l)+(SELECT count(*) FROM m)+(SELECT count(*) FROM n)",
            guild_id,
            retries=5,
        )
        return int(deleted or 0)
