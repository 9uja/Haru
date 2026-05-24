"""유지보수: 대화 기록(chat_history)이 설정 한도를 넘으면 오래된 순으로 정리해 DB를 보호.

평소엔 최대한 보존하고, 행 수가 CHAT_HISTORY_MAX_ROWS 를 넘었을 때만 90%까지 줄인다.
"""
from __future__ import annotations

import logging

from discord.ext import commands, tasks

log = logging.getLogger(__name__)


class Maintenance(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.max_rows = bot.settings.chat_history_max_rows

    async def cog_load(self) -> None:
        self.prune_loop.start()

    async def cog_unload(self) -> None:
        self.prune_loop.cancel()

    @tasks.loop(hours=1)
    async def prune_loop(self) -> None:
        try:
            count = await self.db.count_chat_history()
            if count > self.max_rows:
                keep = int(self.max_rows * 0.9)
                deleted = await self.db.prune_chat_history(keep)
                log.info("대화 기록 정리: %d행 삭제 (%d → %d)", deleted, count, keep)
        except Exception:  # noqa: BLE001 - 정리 실패해도 다음 주기 재시도
            log.warning("대화 기록 정리 실패", exc_info=True)

    @prune_loop.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Maintenance(bot))
