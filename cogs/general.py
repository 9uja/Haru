"""기본 명령어: 핑."""
from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands


class General(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="핑", description="봇의 응답 지연 시간을 확인합니다.")
    async def ping_ko(self, interaction: discord.Interaction) -> None:
        start = time.perf_counter()
        await interaction.response.send_message("측정 중...", ephemeral=True)
        elapsed_ms = (time.perf_counter() - start) * 1000
        gateway_ms = self.bot.latency * 1000
        await interaction.edit_original_response(
            content=f"퐁! 왕복 {elapsed_ms:.0f}ms · 게이트웨이 {gateway_ms:.0f}ms"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
