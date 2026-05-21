"""역할/멤버 관리 명령어. /role add, /role remove (역할 관리 권한 필요)."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    role = app_commands.Group(
        name="role",
        description="멤버 역할 관리",
        default_permissions=discord.Permissions(manage_roles=True),
        guild_only=True,
    )

    def _can_manage(self, guild: discord.Guild, role: discord.Role) -> tuple[bool, str]:
        me = guild.me
        if role >= me.top_role:
            return False, "봇의 최상위 역할보다 같거나 높은 역할은 다룰 수 없습니다."
        if role.is_default():
            return False, "@everyone 역할은 부여/회수할 수 없습니다."
        if role.managed:
            return False, "통합(봇/부스트 등)으로 관리되는 역할은 수동으로 변경할 수 없습니다."
        return True, ""

    @role.command(name="add", description="멤버에게 역할을 부여합니다.")
    @app_commands.describe(member="대상 멤버", role="부여할 역할")
    async def add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        role: discord.Role,
    ) -> None:
        ok, reason = self._can_manage(interaction.guild, role)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return
        if role in member.roles:
            await interaction.response.send_message(
                f"{member.mention} 님은 이미 {role.mention} 역할을 가지고 있습니다.", ephemeral=True
            )
            return
        await member.add_roles(role, reason=f"{interaction.user} 요청")
        await interaction.response.send_message(
            f"{member.mention} 님에게 {role.mention} 역할을 부여했습니다.", ephemeral=True
        )

    @role.command(name="remove", description="멤버의 역할을 회수합니다.")
    @app_commands.describe(member="대상 멤버", role="회수할 역할")
    async def remove(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        role: discord.Role,
    ) -> None:
        ok, reason = self._can_manage(interaction.guild, role)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return
        if role not in member.roles:
            await interaction.response.send_message(
                f"{member.mention} 님은 {role.mention} 역할을 가지고 있지 않습니다.", ephemeral=True
            )
            return
        await member.remove_roles(role, reason=f"{interaction.user} 요청")
        await interaction.response.send_message(
            f"{member.mention} 님의 {role.mention} 역할을 회수했습니다.", ephemeral=True
        )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            msg = "이 명령어를 사용하려면 '역할 관리' 권한이 필요합니다."
        elif isinstance(error, discord.Forbidden):
            msg = "봇에게 역할을 변경할 권한이 없습니다. 봇 역할 위치와 권한을 확인하세요."
        else:
            msg = f"오류가 발생했습니다: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Roles(bot))
