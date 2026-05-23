"""DISBOARD 범프 리마인더.

봇은 다른 봇의 슬래시 명령(/bump)을 대신 실행할 수 없다(Discord API 제약).
대신 DISBOARD 의 범프 성공을 감지해 2시간 뒤 같은 채널에 알림을 보낸다.
예약 시각은 DB에 저장 → 봇 재시작에도 유지.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

log = logging.getLogger(__name__)

DISBOARD_ID = 302050872383242240
BUMP_INTERVAL = timedelta(hours=2)
SILENT = discord.AllowedMentions.none()
# DISBOARD 범프 성공 표시(로케일 차이 대비 여러 마커). 쿨다운/실패 메시지엔 없음.
SUCCESS_MARKERS = ("👍", "bump done", "bumped", "올렸", "올려", "범프 완료")


class Bump(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.guild_id = bot.settings.guild_id

    async def cog_load(self) -> None:
        self.bump_loop.start()

    async def cog_unload(self) -> None:
        self.bump_loop.cancel()

    @staticmethod
    def _is_success(message: discord.Message) -> bool:
        blob = message.content or ""
        for e in message.embeds:
            blob += " " + (e.title or "") + " " + (e.description or "")
        blob = blob.lower()
        return any(marker.lower() in blob for marker in SUCCESS_MARKERS)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.guild.id != self.guild_id:
            return
        if message.author.id != DISBOARD_ID or not self._is_success(message):
            return
        when = datetime.now(timezone.utc) + BUMP_INTERVAL
        try:
            await self.db.set_bump_reminder(self.guild_id, message.channel.id, when)
            log.info("DISBOARD 범프 감지 → 2시간 뒤 리마인더 예약")
            try:
                await message.add_reaction("✅")  # 추적 중 표시(선택)
            except discord.HTTPException:
                pass
        except Exception:
            log.warning("범프 리마인더 예약 실패", exc_info=True)

    @tasks.loop(minutes=1)
    async def bump_loop(self) -> None:
        try:
            guild = self.bot.get_guild(self.guild_id)
            if guild is None:
                return
            channel_id = await self.db.get_due_bump_reminder(self.guild_id)
            if not channel_id:
                return
            await self.db.clear_bump_reminder(self.guild_id)  # 중복 발송 방지로 먼저 비움
            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    "🔔 범프 시간이에요! `/bump` 를 입력해 서버를 올려주세요.",
                    allowed_mentions=SILENT,
                )
        except Exception:
            log.warning("범프 리마인더 처리 실패(다음 주기 재시도)", exc_info=True)

    @bump_loop.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Bump(bot))
