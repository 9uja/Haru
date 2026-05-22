"""AI 대화/번역 기능. "하루야"로 시작하는 메시지를 Google Gemini(무료)로 처리한다.

- `하루야 <질문>`            → 일반 대화
- `하루야 번역 <문장>`        → 한국어↔영어 자동 번역
- `하루야 번역 일본어 <문장>` → 지정 언어로 번역
메시지 본문을 읽으므로 MESSAGE CONTENT INTENT(특권) 필요. GEMINI_API_KEY 없으면 안내만 함.
"""
from __future__ import annotations

import logging

import aiohttp
import discord
from discord.ext import commands

log = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash-lite"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
SYSTEM_HINT = "당신은 '하루'라는 이름의 디스코드 도우미입니다. 한국어로 친근하고 간결하게 답하세요."
TRANSLATE_SYSTEM = "당신은 전문 번역기입니다. 요청한 언어로 자연스럽게 번역하고 번역 결과만 출력하세요(설명·따옴표 없이)."

TRIGGER = "하루야"
TRANSLATE_KEYWORD = "번역"
LANG_MAP = {
    "한국어": "한국어", "한글": "한국어",
    "영어": "영어", "영문": "영어",
    "일본어": "일본어", "일어": "일본어",
    "중국어": "중국어", "중문": "중국어",
    "스페인어": "스페인어", "프랑스어": "프랑스어", "독일어": "독일어",
}
MSG_LIMIT = 2000
SILENT = discord.AllowedMentions.none()


class AIChat(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.api_key = bot.settings.gemini_api_key
        self.guild_id = bot.settings.guild_id
        self.session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        await self.session.close()

    async def _ask(self, prompt: str, system: str = SYSTEM_HINT) -> str:
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
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

    def _build_request(self, prompt: str) -> tuple[str, str]:
        """프롬프트를 (보낼 내용, system) 으로 변환. '번역' 으로 시작하면 번역 모드."""
        if prompt.startswith(TRANSLATE_KEYWORD):
            body = prompt[len(TRANSLATE_KEYWORD):].strip()
            first, _, rest = body.partition(" ")
            if first in LANG_MAP and rest.strip():
                return f"다음 텍스트를 {LANG_MAP[first]}로 번역해줘:\n\n{rest.strip()}", TRANSLATE_SYSTEM
            return (
                f"다음 텍스트가 한국어면 영어로, 아니면 한국어로 번역해줘:\n\n{body}",
                TRANSLATE_SYSTEM,
            )
        return prompt, SYSTEM_HINT

    async def _reply_chunks(self, message: discord.Message, text: str) -> None:
        for i in range(0, len(text), MSG_LIMIT):
            chunk = text[i : i + MSG_LIMIT]
            if i == 0:
                await message.reply(chunk, mention_author=False, allowed_mentions=SILENT)
            else:
                await message.channel.send(chunk, allowed_mentions=SILENT)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.guild.id != self.guild_id:
            return

        content = message.content.strip()
        if not content.startswith(TRIGGER):
            return

        prompt = content[len(TRIGGER):].strip(" \t\n,.!?·~:;")
        if not prompt:
            await message.reply(
                "네! `하루야 <질문>` 으로 대화하거나 `하루야 번역 <문장>` 으로 번역할 수 있어요.",
                mention_author=False,
                allowed_mentions=SILENT,
            )
            return
        if not self.api_key:
            await message.reply(
                "AI 기능이 설정되지 않았습니다. 호스트 `.env` 에 `GEMINI_API_KEY` 를 추가해주세요.",
                mention_author=False,
                allowed_mentions=SILENT,
            )
            return

        user_prompt, system = self._build_request(prompt)
        try:
            async with message.channel.typing():
                answer = await self._ask(user_prompt, system)
        except Exception as exc:  # noqa: BLE001 - 사용자에게 사유 전달
            log.warning("AI 호출 실패", exc_info=True)
            await message.reply(
                f"AI 응답에 실패했습니다: {exc}", mention_author=False, allowed_mentions=SILENT
            )
            return

        await self._reply_chunks(message, answer)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIChat(bot))
