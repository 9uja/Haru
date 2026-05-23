"""AI 대화/번역 기능. "하루야"로 시작하는 메시지를 LLM(무료)으로 처리한다.

- `하루야 <질문>`            → 일반 대화
- `하루야 번역 <문장>`        → 한국어↔영어 자동 번역
- `하루야 번역 일본어 <문장>` → 지정 언어로 번역(+한글 발음)

백엔드: **Gemini 우선, 한도 초과(429) 시 Groq 로 자동 폴백**(둘 다 무료, OpenAI 호환).
대화 방식(트리거·번역·페르소나·발음·쿨다운·일시정지)은 백엔드와 무관하게 동일.
메시지 본문을 읽으므로 MESSAGE CONTENT INTENT(특권) 필요. 키가 하나도 없으면 안내만 함.
"""
from __future__ import annotations

import logging
import random
import re
import time

import aiohttp
import discord
from discord.ext import commands

log = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

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
PAUSE_DEFAULT = 30.0  # 429(한도 초과) 시 기본 일시정지(초)

# 생성 파라미터: 대화는 약간 창의적으로, 번역은 저온도·짧게(반복 폭주 방지)
GEN_CHAT = {"temperature": 0.7, "max_tokens": 800}
GEN_TRANSLATE = {"temperature": 0.2, "max_tokens": 400}
GEN_RANDOM = {"temperature": 0.8, "max_tokens": 200}

RANDOM_SYSTEM = (
    "당신은 '하루'라는 디스코드 친구입니다. 다른 사람들의 대화에 가볍고 자연스럽게 "
    "한두 문장으로 짧게 끼어들어 답하세요. 너무 길거나 진지하지 않게."
)
RANDOM_REPLY_COOLDOWN = 60.0  # 임의 답장 전역 쿨다운(초): 무료 쿼터 보호


class QuotaError(RuntimeError):
    """무료 한도 초과(429). retry_after 초 동안 해당 백엔드 호출을 멈춘다."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _parse_retry(message: str) -> float:
    m = re.search(r"retry in ([\d.]+)s", message) or re.search(r"in ([\d.]+)s", message)
    if m:
        try:
            return min(float(m.group(1)) + 1.0, 120.0)
        except ValueError:
            pass
    return PAUSE_DEFAULT


class AIChat(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.gemini_key = bot.settings.gemini_api_key
        self.groq_key = bot.settings.groq_api_key
        self.groq_model = bot.settings.groq_model
        self.guild_id = bot.settings.guild_id
        self.cooldown = bot.settings.ai_cooldown_seconds
        self.reply_chance = bot.settings.random_reply_chance
        self._last_call: dict[int, float] = {}  # user_id -> 마지막 호출 시각(monotonic)
        self._gemini_pause = 0.0  # 이 시각까지 Gemini 호출 안 함(monotonic)
        self._groq_pause = 0.0
        self._last_random = 0.0  # 마지막 임의 답장 시각(monotonic)
        self.session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        await self.session.close()

    # ------------------------------------------------------------ 백엔드 호출
    async def _call_gemini(self, prompt: str, system: str, gen: dict) -> str:
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": gen["max_tokens"],
                "temperature": gen["temperature"],
            },
        }
        async with self.session.post(
            GEMINI_URL, params={"key": self.gemini_key}, json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                msg = (data.get("error") or {}).get("message", f"HTTP {resp.status}")
                if resp.status == 429:
                    raise QuotaError(msg, _parse_retry(msg))
                raise RuntimeError(msg)
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError("응답을 생성하지 못했습니다(안전 필터 또는 빈 응답).")
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts).strip() or "(빈 응답)"

    async def _call_groq(self, prompt: str, system: str, gen: dict) -> str:
        payload = {
            "model": self.groq_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": gen["max_tokens"],
            "temperature": gen["temperature"],
        }
        headers = {"Authorization": f"Bearer {self.groq_key}"}
        async with self.session.post(
            GROQ_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                msg = (data.get("error") or {}).get("message", f"HTTP {resp.status}")
                if resp.status == 429:
                    ra = resp.headers.get("retry-after")
                    retry = float(ra) + 1.0 if (ra and ra.replace(".", "", 1).isdigit()) else _parse_retry(msg)
                    raise QuotaError(msg, min(retry, 120.0))
                raise RuntimeError(msg)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Groq 빈 응답")
        return (choices[0].get("message", {}).get("content") or "").strip() or "(빈 응답)"

    def _available(self) -> bool:
        """지금 호출 가능한 백엔드가 하나라도 있는지(일시정지·키 고려)."""
        now = time.monotonic()
        if self.gemini_key and now >= self._gemini_pause:
            return True
        if self.groq_key and now >= self._groq_pause:
            return True
        return False

    async def _ask(self, prompt: str, system: str = SYSTEM_HINT, gen: dict = GEN_CHAT) -> str:
        """Gemini 우선, 429/실패 시 Groq 폴백. 모두 불가하면 예외."""
        if self.gemini_key and time.monotonic() >= self._gemini_pause:
            try:
                return await self._call_gemini(prompt, system, gen)
            except QuotaError as exc:
                self._gemini_pause = time.monotonic() + exc.retry_after
                log.warning(
                    "Gemini 한도 초과 — %.0f초 일시중지%s",
                    exc.retry_after, " · Groq 폴백" if self.groq_key else "",
                )
            except Exception:  # noqa: BLE001
                log.warning("Gemini 호출 실패%s", " · Groq 폴백" if self.groq_key else "", exc_info=True)

        if self.groq_key and time.monotonic() >= self._groq_pause:
            try:
                return await self._call_groq(prompt, system, gen)
            except QuotaError as exc:
                self._groq_pause = time.monotonic() + exc.retry_after
                log.warning("Groq 한도 초과 — %.0f초 일시중지", exc.retry_after)
            except Exception:  # noqa: BLE001
                log.warning("Groq 호출 실패", exc_info=True)

        raise RuntimeError("사용 가능한 AI 백엔드가 없습니다.")

    # ------------------------------------------------------------ 프롬프트 구성
    def _build_request(self, prompt: str) -> tuple[str, str, dict]:
        """프롬프트를 (보낼 내용, system, 생성파라미터) 로 변환. '번역' 으로 시작하면 번역 모드."""
        if prompt.startswith(TRANSLATE_KEYWORD):
            body = prompt[len(TRANSLATE_KEYWORD):].strip()
            first, _, rest = body.partition(" ")
            if first in LANG_MAP and rest.strip():
                target, text = LANG_MAP[first], rest.strip()
                if target == "일본어":
                    return (
                        "한국어를 일본어로 번역하고, 그 일본어의 한글 발음을 괄호 안에 적어줘.\n"
                        "설명·따옴표 없이 정확히 두 줄만 출력해.\n"
                        "예) 입력: 안녕하세요\nこんにちは\n(콘니치와)\n\n"
                        f"입력: {text}"
                    ), TRANSLATE_SYSTEM, GEN_TRANSLATE
                return f"다음 문장을 {target}로 번역해줘. 번역문만 출력:\n\n{text}", TRANSLATE_SYSTEM, GEN_TRANSLATE
            return (
                f"다음 문장이 한국어면 영어로, 아니면 한국어로 번역해줘. 번역문만 출력:\n\n{body}",
                TRANSLATE_SYSTEM,
                GEN_TRANSLATE,
            )
        return prompt, SYSTEM_HINT, GEN_CHAT

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
        if content.startswith(TRIGGER):
            await self._handle_trigger(message, content)
        else:
            await self._maybe_random_reply(message, content)

    async def _handle_trigger(self, message: discord.Message, content: str) -> None:
        prompt = content[len(TRIGGER):].strip(" \t\n,.!?·~:;")
        if not prompt:
            await message.reply(
                "네! `하루야 <질문>` 으로 대화하거나 `하루야 번역 <문장>` 으로 번역할 수 있어요.",
                mention_author=False, allowed_mentions=SILENT,
            )
            return
        if not self.gemini_key and not self.groq_key:
            await message.reply(
                "AI 기능이 설정되지 않았습니다. 호스트 `.env` 에 `GEMINI_API_KEY`(또는 `GROQ_API_KEY`)를 추가해주세요.",
                mention_author=False, allowed_mentions=SILENT,
            )
            return

        # 사용자별 쿨다운: 도배 방지·무료 한도 보호. 중복 안내 대신 ⏳ 반응만.
        now = time.monotonic()
        if now - self._last_call.get(message.author.id, 0.0) < self.cooldown:
            try:
                await message.add_reaction("⏳")
            except discord.HTTPException:
                pass
            return
        self._last_call[message.author.id] = now

        # 모든 백엔드가 일시정지 중이면 호출 없이 바로 안내(반복 429·로그 폭주 방지)
        if not self._available():
            await message.reply("지금은 잠시 쉴래요.", mention_author=False, allowed_mentions=SILENT)
            return

        user_prompt, system, gen = self._build_request(prompt)
        try:
            async with message.channel.typing():
                answer = await self._ask(user_prompt, system, gen)
        except Exception:  # noqa: BLE001 - 상세는 _ask 에서 이미 로그, 사용자에겐 통일 문구
            await message.reply("지금은 잠시 쉴래요.", mention_author=False, allowed_mentions=SILENT)
            return

        await self._reply_chunks(message, answer)

    async def _maybe_random_reply(self, message: discord.Message, content: str) -> None:
        """일정 확률로 임의 채팅에 가볍게 답장. 쿼터 보호: 전역 쿨다운·일시정지 존중, 실패는 무시."""
        if self.reply_chance <= 0 or len(content) < 2:
            return
        if random.random() >= self.reply_chance:
            return
        now = time.monotonic()
        if now - self._last_random < RANDOM_REPLY_COOLDOWN or not self._available():
            return
        self._last_random = now
        try:
            async with message.channel.typing():
                answer = await self._ask(content, RANDOM_SYSTEM, GEN_RANDOM)
        except Exception:  # noqa: BLE001 - 임의 답장 실패는 조용히 무시
            return
        await self._reply_chunks(message, answer)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIChat(bot))
