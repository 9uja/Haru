"""재미 기능: 일정 확률로 메시지에 랜덤 이모지 반응을 단다.

- 기본 5% 확률(REACT_CHANCE 로 조절). AI·DB 미사용 → 리소스 거의 없음.
- 봇에 'Add Reactions' 권한 필요(보통 기본 허용).
"""
from __future__ import annotations

import random

import discord
from discord.ext import commands

from http_guard import GUARD

EMOJIS = [
    "😄", "🎉", "👍", "🔥", "✨", "😎", "🥳", "💯", "👀", "🙌",
    "😆", "❤️", "⭐", "🤔", "👏", "🍀", "🌟", "😺", "🎶", "🫡",
]


class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.chance = bot.settings.react_chance
        self.guild_id = bot.settings.guild_id

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.guild.id != self.guild_id:
            return
        if random.random() >= self.chance:
            return
        if GUARD.is_paused():  # 1015 차단 동안엔 이모지 반응 스킵
            return
        try:
            await message.add_reaction(random.choice(EMOJIS))
        except discord.HTTPException:
            pass  # 권한 없음/레이트리밋 등은 조용히 무시


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fun(bot))
