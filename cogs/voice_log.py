"""음성 활동 로깅 + 비활성 멤버 안내.

- /setup-log : 봇 전용 로그 채널 생성
- 음성 입장/퇴장을 로그 채널에 기록하고 last_active 를 DB에 영속화
- /inactive  : 일정 기간 이상 음성 활동이 없는 멤버를 @멘션 형식으로 안내
- 주기적으로 자동 비활성 보고
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from views import MemberListView, build_static_embed, days_ago

log = logging.getLogger(__name__)

SILENT = discord.AllowedMentions.none()  # 멘션을 클릭 가능하게 렌더하되 실제 알림은 보내지 않음


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}초"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}시간")
    if minutes:
        parts.append(f"{minutes}분")
    if not parts:
        parts.append(f"{sec}초")
    return " ".join(parts)


class VoiceLog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.settings = bot.settings
        self.guild_id = bot.settings.guild_id
        self.log_channel_id: Optional[int] = None
        # (guild_id, user_id) -> 세션 시작 시각. 퇴장 시 체류시간 계산용.
        self.session_starts: dict[tuple[int, int], datetime] = {}

    async def cog_load(self) -> None:
        self.log_channel_id = await self.db.get_log_channel(self.guild_id)
        self.heartbeat_loop.start()
        self.report_loop.change_interval(hours=max(self.settings.report_interval_hours, 1))
        self.report_loop.start()

    async def cog_unload(self) -> None:
        self.heartbeat_loop.cancel()
        self.report_loop.cancel()

    # ------------------------------------------------------------------ helpers
    async def _log_channel(self) -> Optional[discord.TextChannel]:
        if self.log_channel_id is None:
            return None
        channel = self.bot.get_channel(self.log_channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _log(self, text: str) -> None:
        channel = await self._log_channel()
        if channel is not None:
            try:
                await channel.send(text, allowed_mentions=SILENT)
            except discord.HTTPException:
                log.warning("로그 채널 전송 실패", exc_info=True)

    # ------------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # 재연결 시에도 캐시 갱신 + 현재 보이스에 있는 멤버 즉시 활동 처리
        self.log_channel_id = await self.db.get_log_channel(self.guild_id)
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            return
        now = datetime.now(timezone.utc)
        for vc in guild.voice_channels:
            for member in vc.members:
                if member.bot:
                    continue
                self.session_starts.setdefault((guild.id, member.id), now)
                await self.db.touch_active(guild.id, member.id, now)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot or member.guild.id != self.guild_id:
            return

        now = datetime.now(timezone.utc)
        key = (member.guild.id, member.id)
        joined = before.channel is None and after.channel is not None
        left = before.channel is not None and after.channel is None

        if joined:
            self.session_starts[key] = now
            await self.db.touch_active(member.guild.id, member.id, now)
            await self._log(f"🔊 {member.mention} 음성 입장 — **{after.channel.name}**")
        elif left:
            start = self.session_starts.pop(key, None)
            seconds = int((now - start).total_seconds()) if start else 0
            await self.db.add_session(member.guild.id, member.id, seconds, now)
            await self._log(f"🔇 {member.mention} 음성 퇴장 — 체류 {_fmt_duration(seconds)}")
        elif after.channel is not None:
            # 채널 이동·음소거 등: 활동 유지로 보고 last_active 만 갱신
            self.session_starts.setdefault(key, now)
            await self.db.touch_active(member.guild.id, member.id, now)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        # 서버(길드) 입장: 나갔다 다시 들어온 횟수 기록
        if member.bot or member.guild.id != self.guild_id:
            return
        now = datetime.now(timezone.utc)
        count = await self.db.record_member_join(member.guild.id, member.id, now)
        await self._log(f"📥 {member.mention} 서버 입장 (누적 {count}회)")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        # 서버(길드) 퇴장: 자진 탈퇴/추방 모두 포함
        if member.bot or member.guild.id != self.guild_id:
            return
        now = datetime.now(timezone.utc)
        count = await self.db.record_member_leave(member.guild.id, member.id, now)
        await self._log(f"📤 {member.mention} (`{member}`) 서버 퇴장 (누적 {count}회)")

    # --------------------------------------------------------------- inactive 계산
    async def _collect_inactive(
        self, guild: discord.Guild, days: int
    ) -> list[tuple[discord.Member, Optional[datetime]]]:
        if not guild.chunked:
            await guild.chunk()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        activity = await self.db.get_activity_map(guild.id)

        result: list[tuple[discord.Member, Optional[datetime]]] = []
        for member in guild.members:
            if member.bot:
                continue
            last = activity.get(member.id)
            if last is None or last < cutoff:
                result.append((member, last))

        oldest = datetime.min.replace(tzinfo=timezone.utc)
        result.sort(key=lambda t: (t[1] is not None, t[1] or oldest))
        return result

    async def _collect_all(
        self, guild: discord.Guild
    ) -> list[tuple[discord.Member, Optional[datetime]]]:
        if not guild.chunked:
            await guild.chunk()
        activity = await self.db.get_activity_map(guild.id)

        result: list[tuple[discord.Member, Optional[datetime]]] = [
            (member, activity.get(member.id)) for member in guild.members if not member.bot
        ]
        # 기록 없음 → 가장 오래 비활성 순으로(관리 관점) 정렬
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        result.sort(key=lambda t: (t[1] is not None, t[1] or oldest))
        return result

    # ------------------------------------------------------------------ commands
    @app_commands.command(name="setup-log", description="봇 전용 로그 채널을 생성합니다.")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(name="생성할 채널 이름 (기본: harubot-log)")
    async def setup_log(self, interaction: discord.Interaction, name: str = "harubot-log") -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        existing = await self.db.get_log_channel(guild.id)
        if existing and guild.get_channel(existing):
            await interaction.response.send_message(
                f"이미 로그 채널이 설정되어 있습니다: <#{existing}>", ephemeral=True
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True
            ),
        }
        try:
            channel = await guild.create_text_channel(
                name=name, overwrites=overwrites, reason=f"{interaction.user} 가 로그 채널 설정"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "채널을 만들 권한이 없습니다. 봇에 '채널 관리' 권한을 부여하세요.", ephemeral=True
            )
            return

        await self.db.set_log_channel(guild.id, channel.id)
        self.log_channel_id = channel.id
        await channel.send("이 채널은 HaruBot 전용 로그 채널로 설정되었습니다. (음성 활동/비활성 보고가 기록됩니다)")
        await interaction.response.send_message(
            f"로그 채널 {channel.mention} 을(를) 생성했습니다. 관리자만 볼 수 있습니다.", ephemeral=True
        )

    @app_commands.command(name="inactive", description="일정 기간 이상 음성 활동이 없는 멤버를 조회합니다.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(days="기준 일수 (미지정 시 기본 설정값)")
    async def inactive(self, interaction: discord.Interaction, days: Optional[int] = None) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        days = days if days and days > 0 else self.settings.inactive_days
        await interaction.response.defer(ephemeral=True)

        rows = await self._collect_inactive(guild, days)
        view = MemberListView(
            author_id=interaction.user.id,
            title=f"📋 {days}일 이상 음성 비활성 멤버",
            rows=rows,
            color=discord.Color.orange(),
        )
        view.message = await interaction.followup.send(
            embed=view.build_embed(), view=view, ephemeral=True
        )

    @app_commands.command(name="activity", description="전체 멤버의 음성 활동 현황을 확인합니다.")
    @app_commands.default_permissions(manage_guild=True)
    async def activity(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        rows = await self._collect_all(guild)
        view = MemberListView(
            author_id=interaction.user.id,
            title="📊 전체 멤버 음성 활동 현황",
            rows=rows,
        )
        view.message = await interaction.followup.send(
            embed=view.build_embed(), view=view, ephemeral=True
        )

    @app_commands.command(name="stats", description="멤버의 음성 활동 통계(입장/퇴장 횟수 등)를 봅니다.")
    @app_commands.describe(member="대상 멤버 (생략 시 본인)")
    async def stats(
        self, interaction: discord.Interaction, member: Optional[discord.Member] = None
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        target = member or interaction.user
        voice = await self.db.get_voice_stats(guild.id, target.id)
        mlog = await self.db.get_member_log(guild.id, target.id)

        s_join = mlog["join_count"] if mlog else 0
        s_leave = mlog["leave_count"] if mlog else 0
        total = voice["total_seconds"] if voice else 0
        last = voice["last_active"] if voice else None

        embed = discord.Embed(
            title=f"{target.display_name} 활동 통계", color=discord.Color.green()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="서버 입장 횟수", value=f"{s_join}회")
        embed.add_field(name="서버 퇴장 횟수", value=f"{s_leave}회")
        embed.add_field(name="​", value="​")  # 줄맞춤용 빈 칸
        embed.add_field(name="누적 음성 체류시간", value=_fmt_duration(total))
        embed.add_field(name="최근 음성 활동", value=days_ago(last))
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ loops
    @tasks.loop(hours=168)
    async def report_loop(self) -> None:
        guild = self.bot.get_guild(self.guild_id)
        channel = await self._log_channel()
        if guild is None or channel is None:
            return
        days = self.settings.inactive_days
        rows = await self._collect_inactive(guild, days)
        embed = build_static_embed(f"🔔 [자동 보고] {days}일 이상 음성 비활성 멤버", rows)
        await channel.send(embed=embed)

    @report_loop.before_loop
    async def _before_report(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=15)
    async def heartbeat_loop(self) -> None:
        # 보이스에 계속 머무는 멤버의 last_active 를 주기적으로 갱신 (입장/퇴장 이벤트만으로는 누락)
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            return
        now = datetime.now(timezone.utc)
        for vc in guild.voice_channels:
            for member in vc.members:
                if not member.bot:
                    await self.db.touch_active(guild.id, member.id, now)

    @heartbeat_loop.before_loop
    async def _before_heartbeat(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceLog(bot))
