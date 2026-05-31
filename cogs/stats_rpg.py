"""RPG 스탯 시스템: 힘/민첩/지능/행운.

규칙
----
- **레벨당 4포인트** 획득, 자유 분배.
- 미분배 = `current_level * 4 - sum(str_pt + agi_pt + int_pt + luk_pt)`.
- 분배 이벤트는 `stat_allocations` 에 1행씩 적재(LIFO 환불용).
- **레벨 손실** 시 가장 최근 분배부터 `(잃은 레벨 × 4)` 포인트를 정확히 환불.
  - 1레벨이든 100레벨이든 동일하게 동작(분배 이력에 전부 기록되어 있으므로).

즉시 효과(현 시스템 반영)
------------------------
- 🧠 **지능**: 메시지·음성 XP 획득 +(`INT_XP_BONUS_PER_PT` × 포인트)
- 🍀 **행운**: 아이템 드롭 확률 ×(1 + `LUK_DROP_BONUS_PER_PT` × 포인트)

미래 효과(보스 레이드 등)
------------------------
- 💪 힘: 데미지
- 💨 민첩: 쿨타임 감소, 치명타 데미지
- 🧠 지능: 마나 재생, 약점 공격 확률(+위 XP 보너스)
- 🍀 행운: 치명타 확률(+위 드롭 보너스)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# leveling 에서 곡선·만렙 상수 재사용
from cogs.leveling import level_from_xp, MAX_LEVEL

log = logging.getLogger(__name__)

POINTS_PER_LEVEL = 4
# 1포인트당 효과(레벨링 코그에서 참조)
INT_XP_BONUS_PER_PT = 0.005   # 지능 1당 XP 획득 +0.5% (100 INT = +50%)
LUK_DROP_BONUS_PER_PT = 0.01  # 행운 1당 드롭 확률 +1% (100 LUK = 2배)

SILENT = discord.AllowedMentions.none()

# stat key → (한국명, 이모지, 효과 설명)
STATS_META: dict[str, tuple[str, str, str]] = {
    "str": ("힘",   "💪", "데미지 _(보스 레이드용)_"),
    "agi": ("민첩", "💨", "쿨타임 감소, 치명타 데미지 _(보스 레이드용)_"),
    "int": ("지능", "🧠", "마나 재생, 약점 공격 확률 _(보스 레이드용)_ · **경험치 획득 +0.5%/포인트**"),
    "luk": ("행운", "🍀", "치명타 확률 _(보스 레이드용)_ · **아이템 드롭 +1%/포인트**"),
}
STAT_KEYS = list(STATS_META.keys())


def unspent_points(level: int, stats_row: Optional[dict]) -> int:
    """현재 레벨 기준 분배 가능한 포인트 수. stats_row 가 없으면 모두 미분배."""
    earned = level * POINTS_PER_LEVEL
    used = 0
    if stats_row is not None:
        used = sum(int(stats_row.get(f"{k}_pt", 0) or 0) for k in STAT_KEYS)
    return max(0, earned - used)


class StatsRPG(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.guild_id = bot.settings.guild_id

    async def cog_load(self) -> None:
        log.info(
            "StatsRPG cog 로드 — 레벨당 %d포인트, 지능 XP+%.1f%%/pt, 행운 드롭+%.0f%%/pt",
            POINTS_PER_LEVEL, INT_XP_BONUS_PER_PT * 100, LUK_DROP_BONUS_PER_PT * 100,
        )

    # ------------------------------------------------------------ 공통 헬퍼
    async def _gather(self, guild_id: int, user_id: int) -> tuple[int, dict, int]:
        """(level, stats_dict, unspent) 한 번에. stats_dict 키: str_pt/agi_pt/int_pt/luk_pt."""
        try:
            xp = await self.db.get_user_xp(guild_id, user_id)
        except Exception:  # noqa: BLE001
            xp = 0
        lv, _, _ = level_from_xp(xp)
        try:
            row = await self.db.get_user_stats(guild_id, user_id)
        except Exception:  # noqa: BLE001
            row = None
        stats = {k + "_pt": int(row[k + "_pt"]) if row else 0 for k in STAT_KEYS}
        return lv, stats, unspent_points(lv, stats)

    # ------------------------------------------------------------ 슬래시: /능력치
    @app_commands.command(name="능력치", description="내 또는 특정 멤버의 RPG 스탯(힘/민첩/지능/행운) 을 봅니다.")
    @app_commands.describe(멤버="확인할 멤버 (생략 시 본인)")
    async def view_stats(
        self, interaction: discord.Interaction, 멤버: Optional[discord.Member] = None
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        target = 멤버 or interaction.user
        if isinstance(target, discord.Member) and target.bot:
            await interaction.response.send_message("봇은 스탯이 없어요.", ephemeral=True)
            return
        ephemeral = 멤버 is None
        await interaction.response.defer(ephemeral=ephemeral)
        lv, stats, unspent = await self._gather(interaction.guild_id, target.id)

        embed = discord.Embed(
            title=f"📜 능력치 — {target.display_name}",
            color=discord.Color.dark_purple(),
            timestamp=datetime.now(timezone.utc),
        )
        avatar = getattr(target, "display_avatar", None)
        if avatar is not None:
            embed.set_thumbnail(url=avatar.url)
        embed.add_field(name="레벨", value=f"**{lv}** / {MAX_LEVEL}", inline=True)
        embed.add_field(name="미분배 포인트", value=f"**{unspent}**", inline=True)
        embed.add_field(
            name="누적 분배",
            value=f"{sum(stats.values()):,}",
            inline=True,
        )
        for key, (name, emoji, desc) in STATS_META.items():
            pt = stats[f"{key}_pt"]
            embed.add_field(
                name=f"{emoji} {name} — **{pt}**",
                value=desc,
                inline=False,
            )
        int_pt = stats["int_pt"]; luk_pt = stats["luk_pt"]
        embed.add_field(
            name="🔢 현재 효과 요약",
            value=(
                f"경험치 획득 **+{int_pt * INT_XP_BONUS_PER_PT * 100:.1f}%** (지능 {int_pt})\n"
                f"아이템 드롭 **×{1 + luk_pt * LUK_DROP_BONUS_PER_PT:.2f}** (행운 {luk_pt})"
            ),
            inline=False,
        )
        embed.set_footer(text="/능력치분배 로 미분배 포인트를 배정")
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    # ------------------------------------------------------------ 슬래시: /능력치분배
    @app_commands.command(name="능력치분배", description="미분배 포인트를 스탯에 할당합니다.")
    @app_commands.describe(스탯="배정할 스탯", 포인트="배정할 포인트 수 (1 이상)")
    @app_commands.choices(스탯=[
        app_commands.Choice(name=f"{emoji} {name}", value=key)
        for key, (name, emoji, _) in STATS_META.items()
    ])
    async def allocate_stat(
        self,
        interaction: discord.Interaction,
        스탯: app_commands.Choice[str],
        포인트: app_commands.Range[int, 1, 999 * POINTS_PER_LEVEL],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        lv, stats, unspent = await self._gather(interaction.guild_id, interaction.user.id)
        if 포인트 > unspent:
            await interaction.followup.send(
                f"미분배 포인트가 부족해요. 현재 보유: **{unspent}**, 요청: **{포인트}**.",
                ephemeral=True,
            )
            return
        key = 스탯.value
        try:
            await self.db.allocate_stat(
                interaction.guild_id, interaction.user.id, key, 포인트,
            )
        except Exception:  # noqa: BLE001
            log.warning("스탯 분배 실패", exc_info=True)
            await interaction.followup.send("DB 처리에 실패했어요.", ephemeral=True)
            return
        name, emoji, _ = STATS_META[key]
        new_total = stats[f"{key}_pt"] + 포인트
        await interaction.followup.send(
            f"{emoji} **{name}** 에 +{포인트} 분배 완료! 현재 {emoji} **{new_total}**, "
            f"미분배 잔여 {unspent - 포인트}.",
            ephemeral=True,
        )
        # 스킬 해방 체크 — 새 분배로 조건 충족 시 DM 알림
        raid_cog = self.bot.get_cog("Raid")
        if raid_cog is not None and hasattr(raid_cog, "check_skill_unlocks"):
            try:
                await raid_cog.check_skill_unlocks(
                    interaction.guild_id, interaction.user.id, notify_user=interaction.user,
                )
            except Exception:  # noqa: BLE001
                log.debug("스킬 해방 체크 실패(스탯분배)", exc_info=True)

    # (구버전 /능력치리셋 명령은 제거됨 — DB 메서드는 오너 디버그/관리용으로 유지)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsRPG(bot))
