"""HaruBot 진입점 — 단일 길드 전용 디스코드 봇."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from config import Settings, load_settings
from database import Database
from keepalive import start_health_server

INITIAL_COGS = (
    "cogs.general",
    "cogs.voice_log",
)


class HaruBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.guild_object = discord.Object(id=settings.guild_id)
        self.db = Database()
        self._health_runner = None

        intents = discord.Intents.default()
        intents.members = True  # 역할/멤버 관리·비활성 조회에 필요한 권한 있는 인텐트
        # voice_states 는 Intents.default() 에 포함되어 음성 이벤트 수신 가능

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            # 저사양 무료 호스트 RAM 절약: 시작 시 전체 멤버 캐시를 받지 않고,
            # /inactive·/activity 등에서 필요할 때 guild.chunk() 로 on-demand 로드
            chunk_guilds_at_startup=False,
        )

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
    bot.run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()
