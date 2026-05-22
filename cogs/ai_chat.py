"""간단한 AI 대화 기능. Google Gemini (AI Studio) 무료 API 사용.

- /ai <메시지> (한국어: /대화) : 한 번의 질문에 한 번 답하는 단순 대화.
- GEMINI_API_KEY 가 없으면 봇은 정상 동작하되 이 명령만 비활성 안내.
- 새 의존성 없이 기존 aiohttp 로 REST 호출.
"""
from __future__ import annotations

import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash-lite"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
SYSTEM_HINT = "당신은 디스코드 서버의 친절한 도우미입니다. 한국어로 간결하게 답하세요."
SILENT = discord.AllowedMentions.none()


class AIChat(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.api_key = bot.settings.gemini_api_key
        self.session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        await self.session.close()

    async def _ask(self, prompt: str) -> str:
        payload = {
            "system_instruction": {"parts": [{"text": SYSTEM_HINT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 800, "temperature": 0.7},
        }
        async with self.session.post(
            API_URL,
            params={"key": self.api_key},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(data.get("error", {}).get("message", f"HTTP {resp.status}"))

        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError("응답을 생성하지 못했습니다(안전 필터 또는 빈 응답).")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or "(빈 응답)"

    async def _ai(self, interaction: discord.Interaction, message: str) -> None:
        if not self.api_key:
            await interaction.response.send_message(
                "AI 기능이 설정되지 않았습니다. 호스트의 `.env` 에 `GEMINI_API_KEY` 를 추가하세요.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        try:
            answer = await self._ask(message)
        except Exception as exc:  # noqa: BLE001 - 사용자에게 사유 전달
            log.warning("AI 호출 실패", exc_info=True)
            await interaction.followup.send(f"AI 응답에 실패했습니다: {exc}", ephemeral=True)
            return

        embed = discord.Embed(description=answer[:4096], color=discord.Color.purple())
        embed.set_author(name=f"💬 {message[:200]}")
        embed.set_footer(text=f"{MODEL} · 무료 티어")
        await interaction.followup.send(embed=embed, allowed_mentions=SILENT)

    @app_commands.command(name="ai", description="AI에게 간단한 질문/대화를 합니다.")
    @app_commands.describe(message="AI에게 보낼 메시지")
    async def ai(self, interaction: discord.Interaction, message: str) -> None:
        await self._ai(interaction, message)

    @app_commands.command(name="대화", description="AI에게 간단한 질문/대화를 합니다.")
    @app_commands.describe(message="AI에게 보낼 메시지")
    async def ai_ko(self, interaction: discord.Interaction, message: str) -> None:
        await self._ai(interaction, message)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIChat(bot))
