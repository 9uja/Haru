"""기본 명령어: 핑, 봇/서버 정보."""
from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands


class General(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="봇의 응답 지연 시간을 확인합니다.")
    async def ping(self, interaction: discord.Interaction) -> None:
        start = time.perf_counter()
        await interaction.response.send_message("측정 중...", ephemeral=True)
        elapsed_ms = (time.perf_counter() - start) * 1000
        gateway_ms = self.bot.latency * 1000
        await interaction.edit_original_response(
            content=f"퐁! 왕복 {elapsed_ms:.0f}ms · 게이트웨이 {gateway_ms:.0f}ms"
        )

    @app_commands.command(name="info", description="현재 서버 정보를 보여줍니다.")
    async def info(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return

        embed = discord.Embed(title=guild.name, color=discord.Color.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="멤버 수", value=str(guild.member_count))
        embed.add_field(name="생성일", value=discord.utils.format_dt(guild.created_at, style="D"))
        if guild.owner:
            embed.add_field(name="소유자", value=guild.owner.mention)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
