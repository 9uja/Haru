"""AI 대화/번역 기능. **봇의 길드 닉네임 + 호격조사**(받침 유 → '아', 무 → '야')로 트리거한다.

봇 이름이 '하루' 면 `하루야 ...`, '준' 이면 `준아 ...`, '루나' 면 `루나야 ...` 처럼
서버에서 봇의 닉네임만 바꾸면 트리거가 즉시 따라가도록 동적 계산한다(코드/재배포 불필요).

- `<봇이름>야/아 <질문>`            → 일반 대화
- `<봇이름>야/아 번역 <문장>`        → 한국어↔영어 자동 번역
- `<봇이름>야/아 번역 일본어 <문장>` → 지정 언어로 번역(+한글 발음)

백엔드: **Gemini 우선, 한도 초과(429) 시 Groq 로 자동 폴백**(둘 다 무료, OpenAI 호환).
대화 방식(트리거·번역·페르소나·발음·쿨다운·일시정지)은 백엔드와 무관하게 동일.
메시지 본문을 읽으므로 MESSAGE CONTENT INTENT(특권) 필요. 키가 하나도 없으면 안내만 함.
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from http_guard import GUARD
from owner import OWNER_ID, OWNER_NICKNAME, is_owner

log = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_HINT_TEMPLATE = (
    "당신은 '{name}'라는 이름의 디스코드 도우미입니다. 한국어로 친근하고 간결하게 답하세요."
)
TRANSLATE_SYSTEM = "당신은 전문 번역기입니다. 요청한 언어로 자연스럽게 번역하고 번역 결과만 출력하세요(설명·따옴표 없이)."

# 봇 이름이 비어있거나 한글 외 문자일 때 마지막 폴백
FALLBACK_BOT_NAME = "하루"
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

RANDOM_SYSTEM_TEMPLATE = (
    "당신은 '{name}'라는 디스코드 친구입니다. 다른 사람들의 대화에 가볍고 자연스럽게 "
    "한두 문장으로 짧게 끼어들어 답하세요. 너무 길거나 진지하지 않게."
)
RANDOM_REPLY_COOLDOWN = 60.0  # 임의 답장 전역 쿨다운(초): 무료 쿼터 보호
KNOWLEDGE_BUDGET = 1500  # 프롬프트에 넣을 서버 지식 최대 문자 수
USER_MEMORY_BUDGET = 800  # 프롬프트에 넣을 개인 기억 최대 문자 수

# 자연어 기억 명령: "<내용> 기억해" / "기억해 <내용>" → 지식 영구 저장(관리자만)
MEMORY_SUFFIX = ("기억해줘", "기억해둬", "기억해", "외워둬", "외워줘", "외워")
MEMORY_PREFIX = ("기억해줘", "기억해둬", "기억해", "외워둬", "외워줘", "외워", "기억하기")

# ───────── 오너 전용 자연어 명령 패턴 ─────────
# 유저 지정: <@id>, <@!id>, 또는 순수 숫자 ID (17~20자리)
USER_REF_RE = re.compile(r"<@!?(\d{15,21})>|\b(\d{15,21})\b")
# "DB 초기화 chat" / "데이터베이스 초기화 all"
ADMIN_DB_RESET_RE = re.compile(
    r"^\s*(?:DB|데이터베이스)\s*초기화\s*(chat|memory|voice|warnings|all)\s*$", re.IGNORECASE
)
# "<유저> 무시 해제" → priority over plain "무시"
ADMIN_UNIGNORE_RE = re.compile(r"무시\s*해제|무시\s*풀어|무시\s*취소")
ADMIN_IGNORE_RE = re.compile(r"\b무시(?:해|해줘|시켜|시켜줘)?\b|차단(?:해|해줘)?")
# "<유저> 지시 해제"
ADMIN_UNINSTRUCT_RE = re.compile(r"지시\s*해제|지시\s*풀어|지시\s*취소")
# "<유저>에게는 …" / "<유저> 한테는 …" / "<유저> 에게 …" → 뒤 내용 = 지시
ADMIN_INSTRUCT_RE = re.compile(
    r"(?:에게는?|한테는?|에게|한테)\s*(.+)$"
)


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
        self.db = bot.db
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
        self.history_turns = bot.settings.chat_history_turns  # 채널별 기억 턴 수(DB 저장)
        self.session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        await self.session.close()

    # ------------------------------------------------------------ 봇 이름 → 트리거(동적)
    @staticmethod
    def _vocative_suffix(name: str) -> str:
        """한국어 호격조사 자동 선택.

        - 마지막 글자가 한글이고 **받침이 있으면** '아' (예: '준' → '준아')
        - 받침이 없으면 '야' (예: '하루' → '하루야')
        - 한글이 아니면 '야' (예: 'Luna' → 'Luna야')
        """
        if not name:
            return "야"
        last = name[-1]
        code = ord(last)
        if 0xAC00 <= code <= 0xD7A3:  # 한글 음절 영역
            return "아" if (code - 0xAC00) % 28 != 0 else "야"
        return "야"

    def _bot_name(self, guild: Optional[discord.Guild] = None) -> str:
        """현재 봇의 표시 이름. 길드 닉네임 우선 → 전역 표시명 → 사용자명 → 폴백.

        - `guild.me.nick` (서버 닉네임) 이 설정돼 있으면 그것을 사용
        - 없으면 `guild.me.display_name` (전역 표시명 또는 username)
        - 길드 정보 부족 시 `bot.user.display_name`/`name`
        """
        if guild is None:
            guild = self.bot.get_guild(self.guild_id)
        if guild is not None and guild.me is not None:
            # nick 가 명시적으로 설정된 경우 최우선
            nick = getattr(guild.me, "nick", None)
            if nick:
                return nick
            nm = guild.me.display_name
            if nm:
                return nm
        user = self.bot.user
        if user is not None:
            return getattr(user, "display_name", None) or user.name or FALLBACK_BOT_NAME
        return FALLBACK_BOT_NAME

    def _trigger(self, guild: Optional[discord.Guild] = None) -> str:
        """현재 봇 이름 + 호격조사. 메시지마다 호출(닉네임 변경에 즉시 반응)."""
        name = self._bot_name(guild)
        return f"{name}{self._vocative_suffix(name)}"

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """기동 시 현재 산출된 트리거를 한 줄 로그(닉네임 변경/배포 후 확인용)."""
        try:
            g = self.bot.get_guild(self.guild_id)
            log.info("AI 트리거 = %r (봇 이름 = %r)", self._trigger(g), self._bot_name(g))
        except Exception:  # noqa: BLE001 - 진단 실패는 무시
            pass

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """봇 자신의 길드 닉네임이 바뀌면 새 트리거를 로그로 알려준다(즉시 적용은 자동)."""
        if self.bot.user is None or after.id != self.bot.user.id:
            return
        if before.nick == after.nick:
            return
        log.info(
            "봇 닉네임 변경: %r → %r → 새 트리거 = %r",
            before.nick, after.nick, self._trigger(after.guild),
        )

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

    async def _ask(self, prompt: str, system: Optional[str] = None, gen: dict = GEN_CHAT) -> str:
        """Gemini 우선, 429/실패 시 Groq 폴백. 모두 불가하면 예외.

        system 이 None 이면 현재 봇 이름으로 기본 페르소나 system 을 생성한다.
        (호출부에서 보통 명시 전달하므로 None 은 안전망 용도)
        """
        if system is None:
            system = SYSTEM_HINT_TEMPLATE.format(name=self._bot_name())
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
    def _build_request(self, prompt: str, bot_name: str) -> tuple[str, str, dict, str]:
        """(보낼 내용, system, 생성파라미터, 모드). 모드: 'translate' | 'chat'.

        bot_name: 현재 봇의 표시 이름. chat 모드 system 페르소나에 주입된다.
        """
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
                    ), TRANSLATE_SYSTEM, GEN_TRANSLATE, "translate"
                return (
                    f"다음 문장을 {target}로 번역해줘. 번역문만 출력:\n\n{text}",
                    TRANSLATE_SYSTEM, GEN_TRANSLATE, "translate",
                )
            return (
                f"다음 문장이 한국어면 영어로, 아니면 한국어로 번역해줘. 번역문만 출력:\n\n{body}",
                TRANSLATE_SYSTEM, GEN_TRANSLATE, "translate",
            )
        return prompt, SYSTEM_HINT_TEMPLATE.format(name=bot_name), GEN_CHAT, "chat"

    async def _knowledge_context(self, guild_id: int) -> str:
        try:
            return await self.db.get_knowledge_context(guild_id, KNOWLEDGE_BUDGET)
        except Exception:  # noqa: BLE001 - 지식 조회 실패해도 대화는 진행
            return ""

    async def _user_memory_context(self, guild_id: int, user_id: int) -> str:
        try:
            return await self.db.get_user_memory_context(guild_id, user_id, USER_MEMORY_BUDGET)
        except Exception:  # noqa: BLE001 - 개인 기억 조회 실패해도 대화는 진행
            return ""

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
        # Cloudflare 1015 가드: 차단 동안엔 어떤 답장(트리거·임의답장·기억 등)도 시도하지 않음.
        # 한 마디 응답이 차단을 더 길게 만들 수 있으므로 조용히 침묵.
        if GUARD.is_paused():
            return

        # 오너 오버라이드 게이트: 'ignore' 모드면 AI가 메시지를 완전히 무시(트리거·임의답장 모두 차단).
        # 오너 본인은 절대 무시되지 않음(안전장치).
        if not is_owner(message.author.id):
            try:
                ov = await self.db.get_user_override(message.guild.id, message.author.id)
            except Exception:  # noqa: BLE001 - 조회 실패해도 진행
                ov = None
            if ov is not None and ov["mode"] == "ignore":
                return

        content = message.content.strip()
        trigger = self._trigger(message.guild)  # 현재 봇 닉네임 기반 (예: '하루야'/'준아')
        if content.startswith(trigger):
            await self._handle_trigger(message, content, trigger)
        else:
            await self._maybe_random_reply(message, content)

    @staticmethod
    def _extract_memory(prompt: str) -> "str | None":
        """기억 명령이면 저장할 내용을 반환(명령 아니면 None). 빈 내용이면 ''."""
        p = prompt.strip()
        for kw in MEMORY_SUFFIX:
            if p.endswith(kw):
                return p[: -len(kw)].strip(" \t\n,.!?·~:")
        for kw in MEMORY_PREFIX:
            if p.startswith(kw):
                return p[len(kw):].strip(" \t\n,.!?·~:")
        return None

    async def _handle_trigger(
        self, message: discord.Message, content: str, trigger: str
    ) -> None:
        prompt = content[len(trigger):].strip(" \t\n,.!?·~:;")
        if not prompt:
            await message.reply(
                f"네! `{trigger} <질문>` 으로 대화하거나, `{trigger} <내용> 기억해` 로 기억시킬 수 있어요.",
                mention_author=False, allowed_mentions=SILENT,
            )
            return

        # 오너 전용 자연어 관리 명령(슬래시 `/관리 …` 와 동등). 일반 유저는 무시되고 일반 대화로 진행.
        if is_owner(message.author.id):
            handled = await self._maybe_handle_owner_command(message, prompt)
            if handled:
                return

        # "<내용> 기억해" → 개인 기억으로 영구 저장(누구나, 본인 대화에서만 참고)
        mem = self._extract_memory(prompt)
        if mem is not None:
            if not mem:
                await message.reply(
                    f"무엇을 기억할까요? 예) `{trigger} 내 생일은 5월 3일 기억해`",
                    mention_author=False, allowed_mentions=SILENT,
                )
                return
            try:
                mid = await self.db.add_user_memory(message.guild.id, message.author.id, mem[:500])
                await message.reply(
                    f"기억했어요! (#{mid}) {message.author.display_name} 님 개인 기억으로 저장했어요. "
                    "(서버 전체 지식은 관리자가 `/기억추가` 로)",
                    mention_author=False, allowed_mentions=SILENT,
                )
            except Exception:  # noqa: BLE001
                log.warning("개인 기억 저장 실패", exc_info=True)
                await message.reply(
                    "지금은 잠시 쉴래요.", mention_author=False, allowed_mentions=SILENT
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

        bot_name = self._bot_name(message.guild)
        user_prompt, system, gen, mode = self._build_request(prompt, bot_name)
        if mode == "chat":
            # 서버 지식 + 개인 기억 주입, 대화 맥락은 채널별+유저별 합쳐서
            kctx = await self._knowledge_context(message.guild.id)
            uctx = await self._user_memory_context(message.guild.id, message.author.id)
            if kctx:
                system = f"{system}\n\n[서버 지식]\n{kctx}"
            if uctx:
                system = f"{system}\n\n[{message.author.display_name} 님 개인 기억]\n{uctx}"
            # 오너 지시 오버라이드: 이 유저에게 어떻게 대응할지 추가 system 지시.
            try:
                ov = await self.db.get_user_override(message.guild.id, message.author.id)
            except Exception:  # noqa: BLE001
                ov = None
            if ov is not None and ov["mode"] == "instruct" and (ov["instruction"] or "").strip():
                system = (
                    f"{system}\n\n[오너 지시 — {message.author.display_name} 대상]\n"
                    f"{ov['instruction'].strip()}"
                )
            try:
                hist = await self.db.get_context(
                    message.guild.id, message.channel.id, message.author.id, self.history_turns * 2
                )
            except Exception:  # noqa: BLE001 - 맥락 조회 실패해도 대화는 진행
                hist = []
            if hist:
                convo = "\n".join(f"{r['role']}: {r['content']}" for r in hist)
                user_prompt = f"[이전 대화]\n{convo}\n\n사용자: {prompt}"

        try:
            async with message.channel.typing():
                answer = await self._ask(user_prompt, system, gen)
        except Exception:  # noqa: BLE001 - 상세는 _ask 에서 이미 로그, 사용자에겐 통일 문구
            await message.reply("지금은 잠시 쉴래요.", mention_author=False, allowed_mentions=SILENT)
            return

        if mode == "chat":
            try:
                await self.db.add_chat_turns(
                    message.guild.id, message.channel.id, message.author.id,
                    [("사용자", prompt), (bot_name, answer)],
                )
            except Exception:  # noqa: BLE001 - 기록 저장 실패는 대화에 영향 없음
                log.warning("대화 기록 저장 실패", exc_info=True)

        await self._reply_chunks(message, answer)

    # ------------------------------------------------------------ 오너 자연어 관리 명령
    @staticmethod
    def _extract_user_id(text: str) -> "tuple[Optional[int], str]":
        """text 에서 첫 유저 참조(<@id> 또는 17~21자리 숫자 ID)를 뽑고, 그 부분을 제거한 잔여 텍스트를 함께 반환."""
        m = USER_REF_RE.search(text)
        if not m:
            return None, text
        uid = int(m.group(1) or m.group(2))
        residual = (text[: m.start()] + " " + text[m.end():]).strip()
        return uid, residual

    async def _resolve_user_id_by_name(
        self, guild: discord.Guild, name: str
    ) -> "Optional[int]":
        """닉네임/유저명으로 길드 멤버를 찾는다. 캐시 → query_members 순. 없으면 None."""
        if not name:
            return None
        # 캐시(있다면) 먼저
        m = discord.utils.get(guild.members, display_name=name) or discord.utils.get(
            guild.members, name=name
        )
        if m is not None:
            return m.id
        try:
            found = await guild.query_members(query=name, limit=5)
        except Exception:  # noqa: BLE001
            return None
        for cand in found:
            if cand.display_name == name or cand.name == name:
                return cand.id
        if len(found) == 1:
            return found[0].id
        return None

    async def _maybe_handle_owner_command(
        self, message: discord.Message, prompt: str
    ) -> bool:
        """오너의 `하루야 <명령>` 자연어 라우팅. 처리되면 True, 아니면 False(일반 대화로)."""
        guild = message.guild
        if guild is None:
            return False

        # 1) DB 초기화: "DB 초기화 <범위>" / "데이터베이스 초기화 <범위>"
        m = ADMIN_DB_RESET_RE.match(prompt)
        if m:
            scope = m.group(1).lower()
            await self._owner_db_reset(message, scope)
            return True

        # 2) 유저 지정 명령들: 멘션/ID 또는 닉네임 첫 토큰 시도
        uid, rest = self._extract_user_id(prompt)
        if uid is None:
            # 닉네임 추정: 첫 단어를 후보로
            # "<닉> 무시" / "<닉>에게는 …" 같이 명령어 키워드가 함께 있을 때만 시도
            if not any(k in prompt for k in ("무시", "차단", "지시", "에게", "한테")):
                return False
            # 가장 가까운 키워드 앞부분을 닉네임으로
            kw_match = re.search(r"(?:무시|차단|지시|에게는?|한테는?)", prompt)
            if kw_match is None:
                return False
            name_part = prompt[: kw_match.start()].strip(" \t\n,.!?·~:;")
            if not name_part:
                return False
            uid = await self._resolve_user_id_by_name(guild, name_part)
            if uid is None:
                await message.reply(
                    f"`{name_part[:50]}` 에 해당하는 멤버를 못 찾았어요. 멘션이나 디스코드 ID로 지정해줘요.",
                    mention_author=False, allowed_mentions=SILENT,
                )
                return True
            rest = prompt[kw_match.start():].strip()

        # 자기 자신(오너) 보호
        if is_owner(uid):
            await message.reply(
                "오너 본인에게는 무시·지시 명령을 적용할 수 없어요.",
                mention_author=False, allowed_mentions=SILENT,
            )
            return True

        # 우선순위: 무시 해제 / 지시 해제 → 지시 → 무시
        if ADMIN_UNIGNORE_RE.search(rest) or ADMIN_UNINSTRUCT_RE.search(rest):
            try:
                ok = await self.db.clear_user_override(guild.id, uid)
            except Exception:  # noqa: BLE001
                log.warning("오버라이드 해제 실패", exc_info=True)
                ok = False
            await message.reply(
                f"🔊 <@{uid}> 오버라이드를 해제했어요." if ok else f"<@{uid}> 에게 설정된 오버라이드가 없어요.",
                mention_author=False, allowed_mentions=SILENT,
            )
            return True

        m2 = ADMIN_INSTRUCT_RE.search(rest)
        if m2:
            instruction = m2.group(1).strip(" \t\n,.!?·~:;")[:400]
            if not instruction:
                _tg = self._trigger(guild)
                await message.reply(
                    f"어떻게 대응할지 지시 내용을 함께 적어줘요. 예) `{_tg} <@123> 에게는 존댓말로만 짧게 답해`",
                    mention_author=False, allowed_mentions=SILENT,
                )
                return True
            try:
                await self.db.set_user_override(guild.id, uid, "instruct", instruction)
            except Exception:  # noqa: BLE001
                log.warning("지시 저장 실패", exc_info=True)
                await message.reply("지시 저장에 실패했어요.", mention_author=False, allowed_mentions=SILENT)
                return True
            await message.reply(
                f"🧭 <@{uid}> 에게 지시를 적용했어요:\n> {instruction}",
                mention_author=False, allowed_mentions=SILENT,
            )
            return True

        if ADMIN_IGNORE_RE.search(rest):
            try:
                await self.db.set_user_override(guild.id, uid, "ignore")
            except Exception:  # noqa: BLE001
                log.warning("무시 저장 실패", exc_info=True)
                await message.reply("무시 설정에 실패했어요.", mention_author=False, allowed_mentions=SILENT)
                return True
            _tg = self._trigger(guild)
            await message.reply(
                f"🔇 <@{uid}> 메시지를 앞으로 무시할게요. (해제: `{_tg} <@{uid}> 무시 해제`)",
                mention_author=False, allowed_mentions=SILENT,
            )
            return True

        return False

    async def _owner_db_reset(self, message: discord.Message, scope: str) -> None:
        """자연어 DB 초기화 — 안전을 위해 슬래시 명령의 버튼 확정 흐름으로 유도."""
        from cogs.admin import SCOPE_LABEL  # 순환 임포트 회피용 지연 임포트
        label = SCOPE_LABEL.get(scope, scope)
        # 자연어 DB 초기화는 슬래시 `/관리 db초기화` 의 안전한 버튼 확정 흐름으로 유도.
        await message.reply(
            f"⚠️ DB 초기화는 안전을 위해 슬래시 명령으로 확정해 주세요.\n"
            f"`/관리 db초기화 범위:{scope}` 를 실행하면 확정 버튼이 나옵니다.\n"
            f"(요청 범위: **{label}**)",
            mention_author=False, allowed_mentions=SILENT,
        )

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
        random_system = RANDOM_SYSTEM_TEMPLATE.format(name=self._bot_name(message.guild))
        try:
            async with message.channel.typing():
                answer = await self._ask(content, random_system, GEN_RANDOM)
        except Exception:  # noqa: BLE001 - 임의 답장 실패는 조용히 무시
            return
        await self._reply_chunks(message, answer)

    # ------------------------------------------------------------ 지식 관리 명령
    @app_commands.command(name="기억추가", description="AI가 답변에 참고할 지식/정보를 저장합니다.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(내용="기억시킬 내용")
    async def knowledge_add(self, interaction: discord.Interaction, 내용: str) -> None:
        text = 내용.strip()[:500]
        if not text:
            await interaction.response.send_message("내용을 입력하세요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        kid = await self.db.add_knowledge(interaction.guild_id, text)
        trigger = self._trigger(interaction.guild)  # 현재 봇 닉네임 기반 예: '하루야'/'준아'
        await interaction.followup.send(
            f"기억했어요! (#{kid}) 앞으로 `{trigger}` 대화에서 참고할게요.", ephemeral=True
        )

    @app_commands.command(name="기억목록", description="저장된 AI 참고 지식을 봅니다.")
    @app_commands.default_permissions(manage_guild=True)
    async def knowledge_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        rows = await self.db.list_knowledge(interaction.guild_id)
        if not rows:
            await interaction.followup.send("저장된 기억이 없어요. `/기억추가` 로 추가하세요.", ephemeral=True)
            return
        lines = [f"`#{r['id']}` {r['content'][:120]}" for r in rows]
        embed = discord.Embed(
            title="🧠 저장된 기억", description="\n".join(lines)[:4000], color=discord.Color.teal()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="기억삭제", description="저장된 서버 지식을 번호로 삭제합니다.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(번호="삭제할 기억 번호(#)")
    async def knowledge_delete(self, interaction: discord.Interaction, 번호: int) -> None:
        await interaction.response.defer(ephemeral=True)
        ok = await self.db.delete_knowledge(interaction.guild_id, 번호)
        await interaction.followup.send(
            "삭제했어요." if ok else f"#{번호} 기억을 찾을 수 없어요.", ephemeral=True
        )

    # ------------------------------------------------------------ 개인 기억 명령(누구나)
    @app_commands.command(name="내기억추가", description="나만의 개인 기억을 저장합니다(내 대화에서만 참고).")
    @app_commands.describe(내용="기억시킬 내용")
    async def user_memory_add(self, interaction: discord.Interaction, 내용: str) -> None:
        text = 내용.strip()[:500]
        if not text:
            await interaction.response.send_message("내용을 입력하세요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        mid = await self.db.add_user_memory(interaction.guild_id, interaction.user.id, text)
        await interaction.followup.send(f"기억했어요! (#{mid}) 내 개인 기억으로 저장했어요.", ephemeral=True)

    @app_commands.command(name="내기억목록", description="내 개인 기억을 봅니다.")
    async def user_memory_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        rows = await self.db.list_user_memory(interaction.guild_id, interaction.user.id)
        if not rows:
            await interaction.followup.send("저장된 개인 기억이 없어요. `/내기억추가` 로 추가하세요.", ephemeral=True)
            return
        lines = [f"`#{r['id']}` {r['content'][:120]}" for r in rows]
        embed = discord.Embed(
            title="🧠 내 개인 기억", description="\n".join(lines)[:4000], color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="내기억삭제", description="내 개인 기억을 번호로 삭제합니다.")
    @app_commands.describe(번호="삭제할 기억 번호(#)")
    async def user_memory_delete(self, interaction: discord.Interaction, 번호: int) -> None:
        await interaction.response.defer(ephemeral=True)
        ok = await self.db.delete_user_memory(interaction.guild_id, interaction.user.id, 번호)
        await interaction.followup.send(
            "삭제했어요." if ok else f"#{번호} 개인 기억을 찾을 수 없어요.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIChat(bot))
