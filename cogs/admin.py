"""봇 오너 전용(`OWNER_ID` 한정) 슬래시 그룹 `/관리`.

서버 권한(관리자 역할 등) 과는 별개로 **고정 디스코드 ID** 한 명만 사용한다.
- `/관리 db초기화 <범위>` — 영역별 DB 초기화 (chat/memory/voice/warnings/all)
- `/관리 무시 <유저>`     — 해당 유저 메시지를 AI가 완전히 무시
- `/관리 무시해제 <유저>` — 무시 해제
- `/관리 지시 <유저> <지시>` — 그 유저와의 대화에서 system 프롬프트에 추가 지시 주입
- `/관리 지시해제 <유저>` — 지시 해제
- `/관리 목록`            — 현재 오버라이드 전체 보기

자연어(`하루야 …`) 명령은 `cogs/ai_chat.py` 에서 라우팅한다.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

from owner import OWNER_ID, OWNER_NICKNAME, is_owner

log = logging.getLogger(__name__)

RESET_SCOPE = Literal["chat", "memory", "voice", "warnings", "all"]
SCOPE_LABEL = {
    "chat": "대화 기록(chat_history)",
    "memory": "서버 지식 + 개인 기억(knowledge, user_memory)",
    "voice": "음성/멤버 통계(voice_activity, member_log)",
    "warnings": "경고(warnings)",
    "all": "전체 사용자 데이터(설정 채널만 보존)",
}

NOT_OWNER = (
    f"이 명령은 봇 오너(`{OWNER_NICKNAME}`, ID `{OWNER_ID}`) 만 사용할 수 있어요."
)


async def _reset_dispatch(db, guild_id: int, scope: str) -> int:
    if scope == "chat":
        return await db.reset_chat_history(guild_id)
    if scope == "memory":
        return await db.reset_memory(guild_id)
    if scope == "voice":
        return await db.reset_voice(guild_id)
    if scope == "warnings":
        return await db.reset_warnings(guild_id)
    if scope == "all":
        return await db.reset_all(guild_id)
    raise ValueError(f"unknown scope: {scope}")


class ConfirmResetView(discord.ui.View):
    """DB 초기화 확정 버튼(오너만 누를 수 있음). 60초 후 자동 해제."""

    def __init__(self, owner_id: int, db, guild_id: int, scope: str) -> None:
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.db = db
        self.guild_id = guild_id
        self.scope = scope
        self.message: Optional[discord.InteractionMessage] = None
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("오너만 누를 수 있어요.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        if self.done:
            return
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(content="⏱️ 시간 초과 — 취소되었습니다.", view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="진짜 초기화", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, _btn: discord.ui.Button) -> None:
        self.done = True
        await interaction.response.defer()
        try:
            n = await _reset_dispatch(self.db, self.guild_id, self.scope)
            text = f"✅ 초기화 완료 — `{SCOPE_LABEL.get(self.scope, self.scope)}` (삭제 {n}건)"
        except Exception as exc:  # noqa: BLE001
            log.warning("DB 초기화 실패: %s", self.scope, exc_info=True)
            text = f"⚠️ 초기화 실패: {exc!s}"
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            await self.message.edit(content=text, view=self)
        self.stop()

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _btn: discord.ui.Button) -> None:
        self.done = True
        await interaction.response.defer()
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            await self.message.edit(content="취소했어요.", view=self)
        self.stop()


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db

    group = app_commands.Group(name="관리", description="봇 오너 전용 관리 명령")

    # ------------------------------------------------------------ 공통 오너 게이트
    async def _ensure_owner(self, interaction: discord.Interaction) -> bool:
        if not is_owner(interaction.user.id):
            if interaction.response.is_done():
                await interaction.followup.send(NOT_OWNER, ephemeral=True)
            else:
                await interaction.response.send_message(NOT_OWNER, ephemeral=True)
            return False
        return True

    # ------------------------------------------------------------ DB 초기화
    @group.command(name="db초기화", description="영역별로 DB를 초기화합니다(오너 전용).")
    @app_commands.describe(범위="초기화 범위")
    async def db_reset(self, interaction: discord.Interaction, 범위: RESET_SCOPE) -> None:
        if not await self._ensure_owner(interaction):
            return
        if interaction.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있어요.", ephemeral=True)
            return
        view = ConfirmResetView(OWNER_ID, self.db, interaction.guild_id, 범위)
        text = (
            f"⚠️ **DB 초기화 확인** — `{SCOPE_LABEL.get(범위, 범위)}` 을 정말 비울까요?\n"
            "60초 안에 아래 버튼을 누르세요. 되돌릴 수 없습니다."
        )
        await interaction.response.send_message(text, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    # ------------------------------------------------------------ 무시/지시
    @group.command(name="무시", description="해당 유저의 메시지를 AI가 완전히 무시합니다(오너 전용).")
    @app_commands.describe(유저="무시할 멤버")
    async def ignore_user(self, interaction: discord.Interaction, 유저: discord.Member) -> None:
        if not await self._ensure_owner(interaction):
            return
        if is_owner(유저.id):
            await interaction.response.send_message("오너 본인은 무시 대상에 설정할 수 없어요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.db.set_user_override(interaction.guild_id, 유저.id, "ignore")
        await interaction.followup.send(
            f"🔇 {유저.mention} 님 메시지를 앞으로 무시할게요. (해제: `/관리 무시해제`)",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @group.command(name="무시해제", description="무시 설정을 해제합니다(오너 전용).")
    @app_commands.describe(유저="해제할 멤버")
    async def unignore_user(self, interaction: discord.Interaction, 유저: discord.Member) -> None:
        if not await self._ensure_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        ok = await self.db.clear_user_override(interaction.guild_id, 유저.id)
        msg = (
            f"🔊 {유저.mention} 님 오버라이드를 해제했어요."
            if ok else f"{유저.mention} 님에게 설정된 오버라이드가 없어요."
        )
        await interaction.followup.send(
            msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @group.command(name="지시", description="해당 유저와의 대화에서 추가 지시를 주입합니다(오너 전용).")
    @app_commands.describe(유저="지시 적용할 멤버", 지시="AI에게 줄 추가 지시(예: '존댓말만 사용')")
    async def instruct_user(
        self, interaction: discord.Interaction, 유저: discord.Member, 지시: str
    ) -> None:
        if not await self._ensure_owner(interaction):
            return
        text = 지시.strip()[:400]
        if not text:
            await interaction.response.send_message("지시 내용을 입력하세요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.db.set_user_override(interaction.guild_id, 유저.id, "instruct", text)
        await interaction.followup.send(
            f"🧭 {유저.mention} 님 대화에 지시를 적용했어요:\n> {text}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @group.command(name="지시해제", description="유저 지시를 해제합니다(오너 전용).")
    @app_commands.describe(유저="해제할 멤버")
    async def uninstruct_user(self, interaction: discord.Interaction, 유저: discord.Member) -> None:
        # 무시해제와 동일 효과(오버라이드 행 자체 삭제)지만 의미상 분리해 사용성↑
        await self.unignore_user.callback(self, interaction, 유저)

    @group.command(name="목록", description="현재 적용된 오버라이드 목록(오너 전용).")
    async def list_overrides(self, interaction: discord.Interaction) -> None:
        if not await self._ensure_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        rows = await self.db.list_user_overrides(interaction.guild_id)
        if not rows:
            await interaction.followup.send("적용된 오버라이드가 없어요.", ephemeral=True)
            return
        lines: list[str] = []
        for r in rows:
            uid = r["user_id"]
            mode = r["mode"]
            ins = (r["instruction"] or "").strip()
            icon = "🔇" if mode == "ignore" else "🧭"
            if mode == "ignore":
                lines.append(f"{icon} <@{uid}> — 무시")
            else:
                lines.append(f"{icon} <@{uid}> — 지시: {ins[:100]}")
        embed = discord.Embed(
            title="⚙️ 유저 오버라이드",
            description="\n".join(lines)[:4000],
            color=discord.Color.dark_gold(),
        )
        await interaction.followup.send(
            embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    # ------------------------------------------------------------ XP 감소(테스트/페널티)
    @group.command(
        name="xp감소",
        description="대상 유저의 XP 를 차감합니다(레벨 하락 시 스탯 자동 환불, 오너 전용).",
    )
    @app_commands.describe(유저="대상 멤버", 양="차감할 XP 양 (>0)")
    async def subtract_xp_cmd(
        self,
        interaction: discord.Interaction,
        유저: discord.Member,
        양: app_commands.Range[int, 1, 10_000_000_000],
    ) -> None:
        if not await self._ensure_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        cog = self.bot.get_cog("Leveling")
        if cog is None or not hasattr(cog, "lose_xp"):
            await interaction.followup.send(
                "레벨링 코그를 찾을 수 없어요.", ephemeral=True
            )
            return
        try:
            result = await cog.lose_xp(interaction.guild_id, 유저.id, int(양))
        except Exception as exc:  # noqa: BLE001
            log.warning("xp감소 실행 실패", exc_info=True)
            await interaction.followup.send(f"실패: {exc!s}", ephemeral=True)
            return
        lost = result["levels_lost"]
        refunded = result["refunded"]
        lines = [
            f"대상: {유저.mention}",
            f"XP: `{result['before_xp']:,}` → `{result['after_xp']:,}`  (−{int(양):,})",
            f"레벨: **{result['before_lv']}** → **{result['after_lv']}**"
            + (f"  (−{lost}레벨)" if lost else ""),
        ]
        total_refund = sum(refunded.values())
        if total_refund > 0:
            parts = [f"{k}+{v}" for k, v in refunded.items() if v > 0]
            lines.append(f"환불된 스탯 포인트(총 {total_refund}): " + ", ".join(parts))
        elif lost:
            lines.append("환불할 분배 이력이 없어 환불 0건.")
        await interaction.followup.send(
            "\n".join(lines), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
