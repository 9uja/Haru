"""HaruBot 진입점 — 단일 길드 전용 디스코드 봇."""
from __future__ import annotations

import logging
import sys

import discord
from discord.ext import commands

from config import Settings, load_settings
from database import Database
from http_guard import HttpGuard, install_http_hook
from keepalive import start_health_server

INITIAL_COGS = (
    "cogs.general",
    "cogs.voice_log",
    "cogs.ai_chat",
    "cogs.fun",
    "cogs.welcome",
    "cogs.moderation",
    "cogs.bump",
    "cogs.maintenance",
    "cogs.admin",  # 오너 전용 /관리 그룹 (OWNER_ID 한정)
    "cogs.leveling",  # 메시지/음성 XP 기반 레벨링 (/레벨, /랭킹)
    "cogs.stats_rpg",  # RPG 스탯 (힘/민첩/지능/행운), 레벨 손실 시 자동 환불
    "cogs.raid",       # 보스 레이드 Phase 1 (fire_golem, 평타 only)
)


class HaruBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.guild_object = discord.Object(id=settings.guild_id)
        self.db = Database()
        self._health_runner = None

        intents = discord.Intents.default()
        intents.members = True  # 멤버 입퇴장·비활성 조회에 필요한 권한 있는 인텐트
        intents.message_content = True  # "하루야 ..." 메시지 본문 읽기(AI 대화)용 권한 인텐트
        # voice_states 는 Intents.default() 에 포함되어 음성 이벤트 수신 가능

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            # 저사양 무료 호스트 RAM 절약: 시작 시 전체 멤버 캐시를 받지 않고,
            # /inactive·/activity 등에서 필요할 때 guild.chunk() 로 on-demand 로드
            chunk_guilds_at_startup=False,
        )
        # Cloudflare 1015(IP 차단) 감지 후 outbound 송신을 자동 일시정지하는 가드.
        # super().__init__() 이후 self.http 가 만들어져 있어야 후킹 가능.
        install_http_hook(self)

    async def setup_hook(self) -> None:
        await self.db.connect(self.settings.database_url)
        logging.info("DB 연결 완료")

        self._health_runner = await start_health_server()

        for cog in INITIAL_COGS:
            await self.load_extension(cog)
            logging.info("코그 로드됨: %s", cog)

        # 글로벌 커맨드를 지정 길드로 복사 후 동기화 → 해당 서버에서 즉시 반영
        self.tree.copy_global_to(guild=self.guild_object)
        synced = await self.tree.sync(guild=self.guild_object)
        logging.info("슬래시 커맨드 %d개 동기화 (guild=%s)", len(synced), self.settings.guild_id)

    async def on_ready(self) -> None:
        logging.info("로그인: %s (id=%s)", self.user, self.user.id if self.user else "?")
        # 지정 길드 외의 서버에 들어가 있으면 자동 탈퇴 (단일 길드 전용 보장)
        for guild in list(self.guilds):
            if guild.id != self.settings.guild_id:
                logging.warning("허용되지 않은 서버에서 나갑니다: %s (%s)", guild.name, guild.id)
                await guild.leave()

    async def close(self) -> None:
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        await self.db.close()
        await super().close()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    bot = HaruBot(settings)
    try:
        bot.run(settings.token, log_handler=None)
    except discord.HTTPException as exc:
        # Cloudflare 1015: 호스트 IP 가 차단된 상태. 자동 재시작이 차단을 더 연장하므로
        # 매우 분명한 메시지와 함께 비정상 종료(코드 2)해서 Wispbyte 의 60초 재시도
        # 차단 패턴(연속 크래시 보호)을 유도한다. 사용자는 6~12시간 STOP 유지 권장.
        if exc.status == 429 and HttpGuard.looks_like_1015(str(exc)):
            border = "=" * 64
            logging.critical(
                "\n%s\nCloudflare 1015 — 호스트 IP 가 일시 차단되었습니다.\n"
                "  · 봇을 **STOP** 한 채로 6~12시간 두세요. 재시작은 차단을 더 길게 만듭니다.\n"
                "  · 노드 변경이 가능하면 Wispbyte 에 IP 교체를 요청하세요.\n"
                "  · 단독 IP 가 필요하면 Oracle Cloud 무료 VM 으로 이전 (docs/DEPLOY.md).\n%s",
                border, border,
            )
            sys.exit(2)
        raise


if __name__ == "__main__":
    main()
