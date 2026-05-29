"""모더레이션: 유저 경고 추가 + /청소 (채널 메시지 일괄 삭제).

- `/유저경고 @유저 <내용>` — 경고 추가 (목록은 /스탯)
- `/청소 <개수>`            — 이 채널의 최근 메시지 N개(1~100) 일괄 삭제, 고정 메시지는 보존
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from http_guard import GUARD

log = logging.getLogger(__name__)

MENTION_USER = discord.AllowedMentions(users=True, roles=False, everyone=False)
MAX_REASON = 500
PURGE_MAX = 100  # Discord bulk_delete 제한
PURGE_MIN = 1


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

    @app_commands.command(name="청소", description="이 채널의 최근 메시지를 일괄 삭제합니다 (고정 메시지 제외, 1~100).")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(개수="삭제할 메시지 수 (1~100)")
    async def purge(
        self,
        interaction: discord.Interaction,
        개수: app_commands.Range[int, PURGE_MIN, PURGE_MAX],
    ) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "텍스트 채널(서버)에서만 사용할 수 있어요.", ephemeral=True
            )
            return

        # 봇이 이 채널에서 실제로 메시지 관리·읽기 권한을 갖고 있는지 확인 (채널 오버라이드 반영)
        me = guild.me
        perms = channel.permissions_for(me) if me is not None else None
        if perms is None or not (perms.manage_messages and perms.read_message_history):
            await interaction.response.send_message(
                "봇에 이 채널의 **메시지 관리** 및 **메시지 기록 읽기** 권한이 필요해요.",
                ephemeral=True,
            )
            return

        # Cloudflare 1015 가드: 임시 차단 중에는 대량 API 호출(bulk_delete) 시도하지 않음
        if GUARD.is_paused():
            await interaction.response.send_message(
                "지금은 잠시 쉴래요. (Discord HTTP 임시 제한 중)", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            # 고정 메시지는 보존(check). bulk_delete 로 14일 이내 메시지를 단일 호출로 처리.
            deleted = await channel.purge(
                limit=개수,
                bulk=True,
                check=lambda m: not m.pinned,
                reason=f"/청소 by {interaction.user} ({interaction.user.id})",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "권한이 부족해 삭제하지 못했어요. (봇 역할 순서/채널 권한 확인)",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            log.warning("청소 실패", exc_info=True)
            await interaction.followup.send(
                f"삭제 중 오류가 발생했어요: `{exc.status}` ({exc.text[:120] if exc.text else ''})",
                ephemeral=True,
            )
            return
        except Exception:  # noqa: BLE001
            log.warning("청소 처리 중 예외", exc_info=True)
            await interaction.followup.send("삭제 중 알 수 없는 오류가 발생했어요.", ephemeral=True)
            return

        log.info(
            "/청소 실행: 채널=%s(%s), 요청=%d, 삭제=%d, 실행자=%s(%s)",
            channel.name, channel.id, 개수, len(deleted),
            interaction.user, interaction.user.id,
        )
        skipped = 개수 - len(deleted)
        note = f" (고정/14일초과 등으로 {skipped}개 제외)" if skipped > 0 else ""
        await interaction.followup.send(
            f"🧹 메시지 **{len(deleted)}개** 를 삭제했어요.{note}", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
