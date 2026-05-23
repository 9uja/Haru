"""모더레이션: 유저 경고 추가. 경고 목록 조회는 /stats(스탯) 에서 제공."""
from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

MENTION_USER = discord.AllowedMentions(users=True, roles=False, everyone=False)
MAX_REASON = 500


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db

    async def _warn(
        self, interaction: discord.Interaction, member: discord.Member, reason: str
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("봇에게는 경고할 수 없습니다.", ephemeral=True)
            return
        if member.id == interaction.user.id:
            await interaction.response.send_message("자기 자신에게는 경고할 수 없습니다.", ephemeral=True)
            return
        reason = reason.strip()[:MAX_REASON]
        if not reason:
            await interaction.response.send_message("경고 내용을 입력하세요.", ephemeral=True)
            return

        await interaction.response.defer()
        now = datetime.now(timezone.utc)
        count = await self.db.add_warning(guild.id, member.id, interaction.user.id, reason, now)
        await interaction.followup.send(
            f"⚠️ {member.mention} 님에게 경고를 부여했습니다. (누적 {count}회)\n사유: {reason}",
            allowed_mentions=MENTION_USER,
        )

    @app_commands.command(name="유저경고", description="유저에게 경고를 추가합니다. (목록은 /스탯 으로 확인)")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(member="대상 유저", reason="경고 내용")
    async def warn_ko(self, interaction: discord.Interaction, member: discord.Member, reason: str) -> None:
        await self._warn(interaction, member, reason)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
