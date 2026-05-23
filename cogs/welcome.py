"""온보딩: 신규 입장 랜덤 환영 + 채널 안내.

- 신규 멤버 입장 시 환영 채널(미설정 시 서버 시스템 채널)에 랜덤 인사.
- /환영채널설정 (welcome-channel): 환영 채널 지정
- /채널안내 (channels): 공개 텍스트 채널과 설명(토픽)을 안내
"""
from __future__ import annotations

import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

WELCOMES = [
    "{member} 님 환영해요! 🎉 편하게 둘러보세요~",
    "어서오세요 {member} 님! 만나서 반가워요 😄",
    "{member} 님이 들어왔어요! 다들 인사해주세요 👋",
    "와아 {member} 님 등장! 🥳 잘 오셨어요~",
    "{member} 님 반가워요! 궁금한 건 언제든 물어보세요 🙌",
    "{member} 님, 우리 서버에 온 걸 환영해요! ✨",
    "반가워요 {member} 님! 좋은 시간 보내세요 🍀",
]

MENTION_USER = discord.AllowedMentions(users=True, roles=False, everyone=False)


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.guild_id = bot.settings.guild_id

    async def _welcome_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        cid = await self.db.get_welcome_channel(guild.id)
        if cid:
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                return ch
        return guild.system_channel  # 미설정 시 서버 기본 시스템 채널

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot or member.guild.id != self.guild_id:
            return
        channel = await self._welcome_channel(member.guild)
        if channel is None:
            return
        greeting = random.choice(WELCOMES).format(member=member.mention)
        greeting += "\n📚 채널이 궁금하면 `/채널안내` 를 입력해보세요!"
        try:
            await channel.send(greeting, allowed_mentions=MENTION_USER)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------ 환영 채널 설정
    async def _set_welcome(
        self, interaction: discord.Interaction, channel: Optional[discord.TextChannel]
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("텍스트 채널을 지정하세요.", ephemeral=True)
            return
        await self.db.set_welcome_channel(guild.id, target.id)
        await interaction.response.send_message(
            f"환영 채널을 {target.mention} 로 설정했습니다.", ephemeral=True
        )

    @app_commands.command(name="환영채널설정", description="신규 입장 환영 메시지를 보낼 채널을 설정합니다.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel="환영 채널 (생략 시 현재 채널)")
    async def welcome_channel_ko(
        self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None
    ) -> None:
        await self._set_welcome(interaction, channel)

    # ------------------------------------------------------------ 채널 안내
    async def _channels(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        embed = discord.Embed(title=f"📚 {guild.name} 채널 안내", color=discord.Color.teal())
        default = guild.default_role
        for category, channels in guild.by_category():
            if len(embed.fields) >= 25:
                break
            lines = []
            for ch in channels:
                if isinstance(ch, discord.TextChannel) and ch.permissions_for(default).view_channel:
                    topic = (ch.topic or "").replace("\n", " ").strip()
                    lines.append(ch.mention + (f" — {topic[:60]}" if topic else ""))
            if lines:
                title = category.name if category else "기타"
                embed.add_field(name=title, value="\n".join(lines)[:1024], inline=False)

        if not embed.fields:
            embed.description = "표시할 공개 채널이 없습니다."
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="채널안내", description="서버 채널 안내를 보여줍니다.")
    async def channels_ko(self, interaction: discord.Interaction) -> None:
        await self._channels(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
