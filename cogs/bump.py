"""DISBOARD 범프 리마인더.

봇은 다른 봇의 슬래시 명령(/bump)을 대신 실행할 수 없다(Discord API 제약).
대신 DISBOARD 의 범프 성공을 감지해, 2시간 뒤 **지정된 채널**에 알림을 보낸다.
알림 채널은 `/범프채널설정` 으로 지정. 예약 시각은 DB에 저장(재시작 유지) + 메모리 캐시
(매분 DB 조회를 피해 Neon 무료 컴퓨트 절약).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger(__name__)

DISBOARD_ID = 302050872383242240
BUMP_INTERVAL = timedelta(hours=2)
SILENT = discord.AllowedMentions.none()
# DISBOARD 범프 성공 표시(로케일 차이 대비 여러 마커). 쿨다운/실패 메시지엔 없음.
SUCCESS_MARKERS = (
    "👍", "thumbsup", "bump done", "bumped",
    "올렸", "끌어올", "올려", "범프 완료", "범프했",
)


class Bump(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.guild_id = bot.settings.guild_id
        self._channel_id: Optional[int] = None       # 지정된 알림 채널(메모리 캐시)
        self._remind_at: Optional[datetime] = None    # 다음 알림 예약 시각(메모리 캐시)

    async def cog_load(self) -> None:
        try:
            row = await self.db.get_bump_state(self.guild_id)
            if row:
                self._channel_id = row["channel_id"]
                self._remind_at = row["remind_at"]
        except Exception:
            log.warning("범프 상태 로드 실패", exc_info=True)
        self.bump_loop.start()

    async def cog_unload(self) -> None:
        self.bump_loop.cancel()

    @staticmethod
    def _disboard_text(message: discord.Message) -> str:
        """DISBOARD 메시지의 모든 텍스트(본문+임베드 제목/본문/푸터/필드)를 합친다."""
        parts = [message.content or ""]
        for e in message.embeds:
            parts.append(e.title or "")
            parts.append(e.description or "")
            if e.footer and e.footer.text:
                parts.append(e.footer.text)
            for f in e.fields:
                parts.append(f"{f.name or ''} {f.value or ''}")
        return " ".join(p for p in parts if p)

    @app_commands.command(name="범프채널설정", description="DISBOARD 범프 리마인더를 보낼 채널을 지정합니다.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(channel="알림 채널 (생략 시 현재 채널)")
    async def set_bump_channel(
        self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("텍스트 채널을 지정하세요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.db.set_bump_channel(guild.id, target.id)
        self._channel_id = target.id
        await interaction.followup.send(
            f"범프 리마인더 채널을 {target.mention} 로 설정했습니다. "
            "DISBOARD 범프가 확인되면 2시간 뒤 이 채널로 알려드릴게요.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.guild.id != self.guild_id:
            return
        if message.author.id != DISBOARD_ID:
            return

        text = self._disboard_text(message)
        success = any(marker.lower() in text.lower() for marker in SUCCESS_MARKERS)
        # 진단 로그: DISBOARD 메시지가 올 때마다 감지 여부·채널설정·실제 텍스트 기록
        log.info(
            "DISBOARD 메시지 (성공감지=%s, 채널설정=%s): %.180s",
            success, self._channel_id is not None, text.replace("\n", " "),
        )
        if not success or self._channel_id is None:
            return

        self._remind_at = datetime.now(timezone.utc) + BUMP_INTERVAL
        try:
            await self.db.schedule_bump_reminder(self.guild_id, self._remind_at)
        except Exception:
            log.warning("범프 예약 저장 실패", exc_info=True)
        try:
            await message.add_reaction("✅")  # 추적 중 표시(선택)
        except discord.HTTPException:
            pass
        log.info("범프 감지 → 2시간 뒤 리마인더 예약")

    @tasks.loop(minutes=1)
    async def bump_loop(self) -> None:
        # 메모리 캐시만 확인(DB 미조회). 예약 시각 도래 시에만 발송.
        if self._remind_at is None or self._channel_id is None:
            return
        if datetime.now(timezone.utc) < self._remind_at:
            return
        self._remind_at = None  # 먼저 비워 중복 발송 방지
        try:
            guild = self.bot.get_guild(self.guild_id)
            channel = guild.get_channel(self._channel_id) if guild else None
            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    "🔔 범프 시간이에요! `/bump` 를 입력해 서버를 올려주세요.",
                    allowed_mentions=SILENT,
                )
            await self.db.clear_bump_reminder(self.guild_id)
        except Exception:
            log.warning("범프 리마인더 발송 실패", exc_info=True)

    @bump_loop.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Bump(bot))
