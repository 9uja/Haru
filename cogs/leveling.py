"""레벨링 + 아이템 드롭 + XP 부스트.

XP 곡선
-------
메이플스토리 클래식 곡선을 참고하되, 채팅 봇 환경에 맞춰 다항식으로 매끄럽게 확장.
L → L+1 필요 XP = `0.5·L² + 5·L + 10`.
- L1=15  (메이플 동일)
- L10=110
- L100=5,010
- L500=127,510
- L999=502,510
누적 합도 BIGINT(2⁶³) 안전 범위. 최대 레벨 999 도달 가능.

토글/채널 설정
--------------
- `guild_config.level_msg_xp_enabled`   (기본 True) — 메시지 XP on/off
- `guild_config.level_voice_xp_enabled` (기본 True) — 음성 XP on/off
- `guild_config.level_up_channel_id`    (NULL=현재 채널) — 레벨업 안내 채널
서버 관리자가 `/레벨설정` 으로 변경.

아이템 드롭
-----------
메시지 XP 적립 시(쿨다운 통과 후)만 드롭 굴림. 기본 확률 `DROP_CHANCE`.
가중치 추첨으로 4종 중 하나를 1개 획득. 인벤토리에 누적.
`/사용 <아이템>` 으로 발동 → **활성 부스트**(유저당 1개)를 새 것으로 교체.

XP 부스트
---------
적립 직전 `active_boosts` 조회해 multiplier 적용. 만료된 부스트는 자동 무시.
새 부스트는 기존 부스트를 덮어쓴다(가장 최근 사용 우선).
"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from http_guard import GUARD
from owner import is_owner

log = logging.getLogger(__name__)

# ───────── XP 정책 ─────────
# 메시지: 1 XP/메시지(고정), 60초 쿨다운으로 도배 방지.
# 음성:   5분 루프, 1틱당 60 XP. (= 시간당 720 XP, 12 XP/min 환산)
MSG_XP = 1
MSG_COOLDOWN = timedelta(seconds=60)
VOICE_TICK = timedelta(minutes=5)
VOICE_XP_PER_TICK = 60
VOICE_MIN_PEERS = 2
MIN_MSG_CHARS = 2
MAX_LEVEL = 999
DROP_CHANCE = 0.02  # 메시지 XP 적립 시 2% 확률로 아이템 드롭

SILENT = discord.AllowedMentions.none()
MENTION_USER = discord.AllowedMentions(users=True, roles=False, everyone=False)


# ───────── 아이템 정의 (코드 상수) ─────────
class Item:
    __slots__ = ("key", "name", "emoji", "multiplier", "duration", "drop_weight", "description")

    def __init__(
        self, key: str, name: str, emoji: str, multiplier: float,
        duration: timedelta, drop_weight: int, description: str,
    ) -> None:
        self.key = key
        self.name = name
        self.emoji = emoji
        self.multiplier = multiplier
        self.duration = duration
        self.drop_weight = drop_weight
        self.description = description

    @property
    def display(self) -> str:
        return f"{self.emoji} {self.name}"


ITEMS: dict[str, Item] = {
    "ration_small": Item(
        key="ration_small", name="작은 비상식량", emoji="🍙",
        multiplier=1.5, duration=timedelta(minutes=30), drop_weight=50,
        description="30분 동안 경험치 +50% (1.5배)",
    ),
    "ration_large": Item(
        key="ration_large", name="큰 비상식량", emoji="🍱",
        multiplier=2.0, duration=timedelta(minutes=20), drop_weight=25,
        description="20분 동안 경험치 2배",
    ),
    "elixir": Item(
        key="elixir", name="엘릭서", emoji="🧪",
        multiplier=3.0, duration=timedelta(minutes=10), drop_weight=10,
        description="10분 동안 경험치 3배",
    ),
    "lucky_charm": Item(
        key="lucky_charm", name="행운의 부적", emoji="🍀",
        multiplier=5.0, duration=timedelta(minutes=5), drop_weight=3,
        description="5분 동안 경험치 5배 (희귀)",
    ),
}


def _pick_drop() -> Item:
    """가중치 기반 1개 추첨."""
    pool = list(ITEMS.values())
    return random.choices(pool, weights=[i.drop_weight for i in pool], k=1)[0]


# ───────── 레벨 곡선 ─────────
def xp_for_next_level(level: int) -> int:
    """L → L+1 로 가기 위해 필요한 XP. 메이플 클래식 곡선 영감 (L1=15, L999 도달 가능)."""
    if level < 1:
        return 15
    if level >= MAX_LEVEL:
        return 0  # MAX 도달 시 더 진행 X
    return int(0.5 * level * level + 5 * level + 10)


def level_from_xp(total_xp: int) -> tuple[int, int, int]:
    """누적 XP → (현재 레벨, 이 레벨에서 보유 xp, 다음 레벨까지 필요한 xp)."""
    level = 0
    remaining = max(int(total_xp), 0)
    need = xp_for_next_level(level)
    while need > 0 and remaining >= need and level < MAX_LEVEL:
        remaining -= need
        level += 1
        need = xp_for_next_level(level)
    return level, remaining, need


def progress_bar(cur: int, need: int, width: int = 16) -> str:
    if need <= 0:
        return "▰" * width
    filled = min(width, max(0, round(width * cur / need)))
    return "▰" * filled + "▱" * (width - filled)


def _fmt_remaining(expires_at: datetime) -> str:
    delta = expires_at - datetime.now(timezone.utc)
    secs = max(0, int(delta.total_seconds()))
    m, s = divmod(secs, 60)
    return f"{m}분 {s}초" if m else f"{s}초"


class Leveling(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.guild_id = bot.settings.guild_id
        self._last_msg_xp: dict[int, float] = {}

    async def cog_load(self) -> None:
        log.info(
            "Leveling cog 로드 — 메시지 XP %d/60s, 음성 XP %d/%dmin (peers≥%d), drop=%.0f%%, max_level=%d",
            MSG_XP, VOICE_XP_PER_TICK, int(VOICE_TICK.total_seconds() // 60),
            VOICE_MIN_PEERS, DROP_CHANCE * 100, MAX_LEVEL,
        )
        self.voice_xp_loop.start()
        self.boost_cleanup_loop.start()

    async def cog_unload(self) -> None:
        self.voice_xp_loop.cancel()
        self.boost_cleanup_loop.cancel()

    # ------------------------------------------------------------ 공통 헬퍼
    async def _get_multiplier(self, guild_id: int, user_id: int) -> float:
        try:
            row = await self.db.get_active_boost(guild_id, user_id)
        except Exception:  # noqa: BLE001
            return 1.0
        return float(row["multiplier"]) if row else 1.0

    async def _get_int_xp_bonus(self, guild_id: int, user_id: int) -> float:
        """지능 스탯 → 추가 XP 배수 (1.0 = 효과 없음). DB/모듈 의존 없이 안전 폴백."""
        try:
            from cogs.stats_rpg import INT_XP_BONUS_PER_PT
            row = await self.db.get_user_stats(guild_id, user_id)
        except Exception:  # noqa: BLE001
            return 1.0
        if row is None:
            return 1.0
        return 1.0 + int(row["int_pt"]) * INT_XP_BONUS_PER_PT

    async def _get_luk_drop_bonus(self, guild_id: int, user_id: int) -> float:
        """행운 스탯 → 드롭 확률 배수 (1.0 = 효과 없음)."""
        try:
            from cogs.stats_rpg import LUK_DROP_BONUS_PER_PT
            row = await self.db.get_user_stats(guild_id, user_id)
        except Exception:  # noqa: BLE001
            return 1.0
        if row is None:
            return 1.0
        return 1.0 + int(row["luk_pt"]) * LUK_DROP_BONUS_PER_PT

    async def _is_ignored(self, guild_id: int, user_id: int) -> bool:
        if is_owner(user_id):
            return False
        try:
            ov = await self.db.get_user_override(guild_id, user_id)
        except Exception:  # noqa: BLE001
            return False
        return ov is not None and ov["mode"] == "ignore"

    async def _level_up_target(
        self, guild: discord.Guild, fallback: discord.abc.Messageable
    ) -> discord.abc.Messageable:
        """레벨업 안내를 보낼 채널: 설정돼 있으면 그 채널, 아니면 fallback(트리거 채널)."""
        try:
            cfg = await self.db.get_level_config(guild.id)
        except Exception:  # noqa: BLE001
            cfg = None
        ch_id = cfg["ch"] if cfg else None
        if ch_id:
            ch = guild.get_channel(ch_id)
            if isinstance(ch, discord.TextChannel):
                return ch
        return fallback

    async def _maybe_announce_levelup(
        self,
        guild: discord.Guild,
        trigger_channel: Optional[discord.abc.Messageable],
        member: discord.abc.User,
        before_xp: int,
        after_xp: int,
    ) -> None:
        before_lv, _, _ = level_from_xp(before_xp)
        after_lv, _, _ = level_from_xp(after_xp)
        if after_lv <= before_lv:
            return
        # 1) 채널 안내
        if trigger_channel is not None and not GUARD.is_paused():
            target = await self._level_up_target(guild, trigger_channel)
            try:
                await target.send(
                    f"🎉 {member.mention} 님이 **레벨 {after_lv}** 이 됐어요!",
                    allowed_mentions=MENTION_USER,
                )
            except discord.HTTPException:
                pass
        # 2) 스킬 해방 체크 — Raid 코그에 위임(있을 때만, DM 알림 포함)
        raid_cog = self.bot.get_cog("Raid")
        if raid_cog is not None and hasattr(raid_cog, "check_skill_unlocks"):
            try:
                await raid_cog.check_skill_unlocks(guild.id, member.id, notify_user=member)
            except Exception:  # noqa: BLE001
                log.debug("스킬 해방 체크 실패(레벨업)", exc_info=True)

    async def _maybe_drop_item(
        self, channel: discord.abc.Messageable, member: discord.abc.User
    ) -> None:
        if GUARD.is_paused():
            return
        guild = getattr(member, "guild", None)
        guild_id = guild.id if guild is not None else self.guild_id
        luk_bonus = await self._get_luk_drop_bonus(guild_id, member.id)
        # 1.0 보장(상한 없음 → 행운 100 = 2배, 400 = 5배 등). 0.95 캡(완전 100% 는 비현실)
        effective = min(0.95, DROP_CHANCE * luk_bonus)
        if random.random() >= effective:
            return
        item = _pick_drop()
        try:
            await self.db.add_to_inventory(member.guild.id, member.id, item.key, 1)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            log.debug("아이템 적립 실패", exc_info=True)
            return
        try:
            await channel.send(
                f"✨ {member.mention} 이(가) {item.emoji} **{item.name}** 을(를) 획득했어요!\n"
                f"`/사용` 으로 발동 — {item.description}",
                allowed_mentions=MENTION_USER,
            )
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------ 메시지 XP
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.guild.id != self.guild_id:
            return
        content = (message.content or "").strip()
        if len(content) < MIN_MSG_CHARS:
            return
        if content.startswith(("!", "?", "/", ".")):
            return

        # 설정 확인: 메시지 XP 비활성화면 즉시 종료
        try:
            cfg = await self.db.get_level_config(message.guild.id)
        except Exception:  # noqa: BLE001
            cfg = None
        msg_enabled = cfg["msg"] if cfg is not None else True
        if not msg_enabled:
            return

        if await self._is_ignored(message.guild.id, message.author.id):
            return

        uid = message.author.id
        now_mono = time.monotonic()
        last = self._last_msg_xp.get(uid)
        if last is not None and now_mono - last < MSG_COOLDOWN.total_seconds():
            return
        if last is None:
            try:
                db_last = await self.db.get_msg_xp_cooldown(message.guild.id, uid)
            except Exception:  # noqa: BLE001
                db_last = None
            if db_last is not None and datetime.now(timezone.utc) - db_last < MSG_COOLDOWN:
                self._last_msg_xp[uid] = now_mono - (
                    MSG_COOLDOWN.total_seconds()
                    - (datetime.now(timezone.utc) - db_last).total_seconds()
                )
                return

        mult = await self._get_multiplier(message.guild.id, uid)
        int_bonus = await self._get_int_xp_bonus(message.guild.id, uid)
        # 메시지는 1 XP/메시지 고정. 부스트·지능 보너스만 곱연산(최소 1 보장).
        gained = max(1, int(round(MSG_XP * mult * int_bonus)))
        try:
            before, after = await self.db.add_message_xp(
                message.guild.id, uid, gained, datetime.now(timezone.utc)
            )
        except Exception:  # noqa: BLE001
            log.warning("메시지 XP 적립 실패", exc_info=True)
            return
        self._last_msg_xp[uid] = now_mono
        await self._maybe_announce_levelup(
            message.guild, message.channel, message.author, before, after
        )
        await self._maybe_drop_item(message.channel, message.author)

    # ------------------------------------------------------------ 음성 XP (5분마다 1틱)
    @tasks.loop(minutes=5)
    async def voice_xp_loop(self) -> None:
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            return
        try:
            cfg = await self.db.get_level_config(guild.id)
        except Exception:  # noqa: BLE001
            cfg = None
        if cfg is not None and not cfg["voice"]:
            return  # 음성 XP 비활성화
        for ch in guild.voice_channels:
            humans = [m for m in ch.members if not m.bot]
            if len(humans) < VOICE_MIN_PEERS:
                continue
            for m in humans:
                vs = m.voice
                if vs is None or vs.self_deaf or vs.deaf or vs.afk:
                    continue
                if await self._is_ignored(guild.id, m.id):
                    continue
                mult = await self._get_multiplier(guild.id, m.id)
                int_bonus = await self._get_int_xp_bonus(guild.id, m.id)
                # 1틱(5분) 당 60 XP 기본. 부스트·지능 보너스 곱연산.
                amount = max(1, int(round(VOICE_XP_PER_TICK * mult * int_bonus)))
                try:
                    await self.db.add_voice_xp(guild.id, m.id, amount)
                except Exception:  # noqa: BLE001
                    log.debug("음성 XP 적립 실패", exc_info=True)

    @voice_xp_loop.before_loop
    async def _voice_before_loop(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ XP 손실 공개 API
    # 도박/감소 등 향후 기능이 이 메서드 하나만 호출하면 안전하게 처리됨:
    # 1) XP 차감 → 2) 레벨 비교 → 3) 레벨이 떨어졌다면 (잃은 레벨 * 4) 포인트 LIFO 환불
    # 1레벨이든 100레벨 손실이든 동일 로직(분배 이력에 전부 기록되어 있어 정확히 복원).
    POINTS_PER_LEVEL = 4  # stats_rpg 와 동일 의미. 순환 임포트 회피용 로컬 상수.

    async def lose_xp(
        self, guild_id: int, user_id: int, amount: int
    ) -> dict:
        """XP 를 `amount` 만큼 차감. 레벨이 떨어지면 스탯 환불도 수행.
        반환:
            {
              "before_xp": int, "after_xp": int,
              "before_lv": int, "after_lv": int,
              "levels_lost": int,
              "refunded": {"str": int, "agi": int, "int": int, "luk": int},
            }
        """
        try:
            before, after = await self.db.subtract_xp(guild_id, user_id, max(0, int(amount)))
        except Exception:  # noqa: BLE001
            log.warning("XP 차감 실패", exc_info=True)
            return {"before_xp": 0, "after_xp": 0, "before_lv": 0, "after_lv": 0,
                    "levels_lost": 0, "refunded": {"str": 0, "agi": 0, "int": 0, "luk": 0}}
        before_lv, _, _ = level_from_xp(before)
        after_lv, _, _ = level_from_xp(after)
        levels_lost = max(0, before_lv - after_lv)
        refunded = {"str": 0, "agi": 0, "int": 0, "luk": 0}
        if levels_lost > 0:
            try:
                refunded = await self.db.refund_stat_points(
                    guild_id, user_id, levels_lost * self.POINTS_PER_LEVEL,
                )
            except Exception:  # noqa: BLE001
                log.warning("스탯 환불 실패(레벨 손실)", exc_info=True)
        return {
            "before_xp": before, "after_xp": after,
            "before_lv": before_lv, "after_lv": after_lv,
            "levels_lost": levels_lost, "refunded": refunded,
        }

    # ------------------------------------------------------------ 만료 부스트 정리(1시간)
    @tasks.loop(hours=1)
    async def boost_cleanup_loop(self) -> None:
        try:
            n = await self.db.clear_expired_boosts()
            if n:
                log.debug("만료 부스트 %d개 정리", n)
        except Exception:  # noqa: BLE001
            log.debug("부스트 정리 실패", exc_info=True)

    @boost_cleanup_loop.before_loop
    async def _boost_before_loop(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ 슬래시: /레벨
    @app_commands.command(name="레벨", description="내 또는 특정 멤버의 레벨/XP 를 확인합니다.")
    @app_commands.describe(멤버="확인할 멤버 (생략 시 본인)")
    async def my_level(
        self, interaction: discord.Interaction, 멤버: Optional[discord.Member] = None
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        target = 멤버 or interaction.user
        if isinstance(target, discord.Member) and target.bot:
            await interaction.response.send_message("봇은 레벨이 없어요.", ephemeral=True)
            return
        ephemeral = 멤버 is None
        await interaction.response.defer(ephemeral=ephemeral)
        try:
            total = await self.db.get_user_xp(interaction.guild_id, target.id)
            rank, total_users = await self.db.get_user_rank(interaction.guild_id, target.id)
            boost = await self.db.get_active_boost(interaction.guild_id, target.id)
        except Exception:  # noqa: BLE001
            log.warning("레벨 조회 실패", exc_info=True)
            await interaction.followup.send("DB 조회에 실패했어요.", ephemeral=True)
            return
        lv, cur, need = level_from_xp(total)
        bar = progress_bar(cur, need)
        embed = discord.Embed(
            title=f"🏅 레벨 — {target.display_name}",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        avatar = getattr(target, "display_avatar", None)
        if avatar is not None:
            embed.set_thumbnail(url=avatar.url)
        embed.add_field(name="레벨", value=f"**{lv}** / {MAX_LEVEL}", inline=True)
        embed.add_field(name="누적 XP", value=f"`{total:,}`", inline=True)
        embed.add_field(
            name="서버 순위",
            value=(f"#{rank} / {total_users}" if rank else "—"),
            inline=True,
        )
        if lv >= MAX_LEVEL:
            embed.add_field(name="🌟 만렙 달성", value="다음 레벨이 없어요!", inline=False)
        else:
            embed.add_field(
                name=f"다음 레벨까지  {cur:,} / {need:,}",
                value=f"`{bar}` {int(cur*100/max(need,1))}%",
                inline=False,
            )
        if boost:
            mult = float(boost["multiplier"])
            embed.add_field(
                name="⚡ 활성 부스트",
                value=f"**x{mult:g}** — 남은 시간 {_fmt_remaining(boost['expires_at'])}",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    # ------------------------------------------------------------ 슬래시: /랭킹
    @app_commands.command(name="랭킹", description="서버 레벨 상위 10명을 봅니다.")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            rows = await self.db.top_xp(interaction.guild_id, limit=10)
        except Exception:  # noqa: BLE001
            log.warning("랭킹 조회 실패", exc_info=True)
            await interaction.followup.send("DB 조회에 실패했어요.", ephemeral=True)
            return
        if not rows:
            await interaction.followup.send("아직 XP 기록이 없어요. 채팅·음성을 즐겨보세요!", ephemeral=True)
            return
        guild = interaction.guild
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines: list[str] = []
        for i, r in enumerate(rows, start=1):
            uid = int(r["user_id"])
            xp = int(r["xp"])
            lv, _, _ = level_from_xp(xp)
            member = guild.get_member(uid)
            name = member.display_name if member is not None else f"<@{uid}>"
            head = medals.get(i, f"`#{i:>2}`")
            lines.append(f"{head} **{name}** — Lv {lv}  ·  {xp:,} XP")
        embed = discord.Embed(
            title="🏆 서버 랭킹 TOP 10",
            description="\n".join(lines),
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.followup.send(embed=embed, allowed_mentions=SILENT)

    # ------------------------------------------------------------ 슬래시: /인벤토리
    @app_commands.command(name="인벤토리", description="보유 아이템과 활성 부스트를 봅니다.")
    async def inventory(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            inv = await self.db.list_inventory(interaction.guild_id, interaction.user.id)
            boost = await self.db.get_active_boost(interaction.guild_id, interaction.user.id)
        except Exception:  # noqa: BLE001
            log.warning("인벤토리 조회 실패", exc_info=True)
            await interaction.followup.send("DB 조회에 실패했어요.", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"🎒 인벤토리 — {interaction.user.display_name}",
            color=discord.Color.dark_teal(),
            timestamp=datetime.now(timezone.utc),
        )
        if boost:
            embed.add_field(
                name="⚡ 활성 부스트",
                value=f"**x{float(boost['multiplier']):g}** — 남은 시간 {_fmt_remaining(boost['expires_at'])}",
                inline=False,
            )
        if not inv:
            embed.description = "아직 아이템이 없어요. 채팅을 하다 보면 드롭됩니다!"
        else:
            lines = []
            for r in inv:
                key = r["item_key"]
                qty = int(r["qty"])
                it = ITEMS.get(key)
                if it is None:
                    lines.append(f"❓ `{key}` × {qty}")
                else:
                    lines.append(f"{it.emoji} **{it.name}** × {qty}  —  _{it.description}_")
            embed.add_field(name="보유 아이템", value="\n".join(lines), inline=False)
        embed.set_footer(text="/사용 으로 발동 (현재 부스트가 있으면 덮어쓰기)")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------ 슬래시: /사용
    @app_commands.command(name="사용", description="보유한 부스트 아이템을 발동합니다.")
    @app_commands.describe(아이템="발동할 아이템")
    @app_commands.choices(아이템=[
        app_commands.Choice(name=f"{it.emoji} {it.name} — {it.description}", value=key)
        for key, it in ITEMS.items()
    ])
    async def use_item(
        self, interaction: discord.Interaction, 아이템: app_commands.Choice[str]
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        key = 아이템.value
        item = ITEMS.get(key)
        if item is None:
            await interaction.response.send_message("알 수 없는 아이템이에요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            ok = await self.db.consume_item(interaction.guild_id, interaction.user.id, key)
        except Exception:  # noqa: BLE001
            log.warning("아이템 소비 실패", exc_info=True)
            await interaction.followup.send("DB 처리에 실패했어요.", ephemeral=True)
            return
        if not ok:
            await interaction.followup.send(
                f"{item.display} 을(를) 보유하고 있지 않아요. (`/인벤토리` 로 확인)",
                ephemeral=True,
            )
            return
        expires = datetime.now(timezone.utc) + item.duration
        try:
            await self.db.set_active_boost(
                interaction.guild_id, interaction.user.id, item.multiplier, expires,
            )
        except Exception:  # noqa: BLE001
            log.warning("부스트 적용 실패", exc_info=True)
            await interaction.followup.send("부스트 적용에 실패했어요.", ephemeral=True)
            return
        await interaction.followup.send(
            f"⚡ {item.display} 발동! **x{item.multiplier:g}** 부스트가 "
            f"{int(item.duration.total_seconds() // 60)}분 동안 적용돼요.",
            ephemeral=True,
        )

    # ------------------------------------------------------------ 슬래시: /레벨설정 (서버 관리)
    @app_commands.command(name="레벨설정", description="레벨링 옵션(XP 소스 토글·알림 채널)을 변경합니다.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        메시지xp="채팅으로 XP 적립 활성/비활성",
        음성xp="음성 활동으로 XP 적립 활성/비활성",
        알림채널="레벨업 안내를 보낼 채널 (생략 시 변경 없음)",
        알림채널해제="True 면 알림 채널 설정을 해제 — 트리거 채널에서 안내",
    )
    async def level_settings(
        self,
        interaction: discord.Interaction,
        메시지xp: Optional[bool] = None,
        음성xp: Optional[bool] = None,
        알림채널: Optional[discord.TextChannel] = None,
        알림채널해제: Optional[bool] = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.db.set_level_config(
                interaction.guild_id,
                msg_xp_enabled=메시지xp,
                voice_xp_enabled=음성xp,
                level_up_channel_id=(알림채널.id if 알림채널 is not None else None),
                clear_channel=bool(알림채널해제),
            )
            cfg = await self.db.get_level_config(interaction.guild_id)
        except Exception:  # noqa: BLE001
            log.warning("레벨 설정 변경 실패", exc_info=True)
            await interaction.followup.send("DB 처리에 실패했어요.", ephemeral=True)
            return
        msg_on = bool(cfg["msg"]) if cfg else True
        voice_on = bool(cfg["voice"]) if cfg else True
        ch_id = cfg["ch"] if cfg else None
        ch_text = f"<#{ch_id}>" if ch_id else "_(현재 채널에서 안내)_"
        embed = discord.Embed(
            title="⚙️ 레벨 설정",
            color=discord.Color.green(),
            description=(
                f"메시지 XP: **{'활성' if msg_on else '비활성'}**\n"
                f"음성 XP: **{'활성' if voice_on else '비활성'}**\n"
                f"레벨업 안내 채널: {ch_text}"
            ),
        )
        await interaction.followup.send(
            embed=embed, ephemeral=True, allowed_mentions=SILENT,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Leveling(bot))
