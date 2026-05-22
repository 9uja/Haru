"""멤버 목록용 인터랙티브 임베드 페이지네이터.

버튼: ◀️ 이전 / ▶️ 다음 / 🔃 정렬 변경(비활성순·활동순·이름순). 명령 실행자만 조작 가능.
임베드 안의 멘션은 클릭은 되지만 알림(핑)은 가지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

import discord

PER_PAGE = 10
Row = tuple[discord.Member, Optional[datetime]]
LineFn = Callable[[discord.Member, Optional[datetime]], str]


def days_ago(last: Optional[datetime]) -> str:
    if last is None:
        return "기록 없음"
    days = (datetime.now(timezone.utc) - last).days
    return "오늘" if days <= 0 else f"{days}일 전"


def default_line(member: discord.Member, last: Optional[datetime]) -> str:
    return f"• {member.mention} — 최근 활동: {days_ago(last)}"


def _min_dt() -> datetime:
    return datetime.min.replace(tzinfo=timezone.utc)


def _sort_inactive(rows: list[Row]) -> list[Row]:
    # 기록 없음 → 가장 오래 비활성 → 최근 활동
    return sorted(rows, key=lambda t: (t[1] is not None, t[1] or _min_dt()))


def _sort_active(rows: list[Row]) -> list[Row]:
    # 최근 활동 → ... → 기록 없음(맨 뒤)
    return sorted(rows, key=lambda t: t[1] or _min_dt(), reverse=True)


def _sort_name(rows: list[Row]) -> list[Row]:
    return sorted(rows, key=lambda t: t[0].display_name.lower())


# (라벨, 이모지, 정렬 함수)
SORT_MODES: list[tuple[str, str, Callable[[list[Row]], list[Row]]]] = [
    ("비활성 순", "⬇️", _sort_inactive),
    ("활동 순", "⬆️", _sort_active),
    ("이름 순", "🔤", _sort_name),
]


def build_static_embed(
    title: str,
    rows: list[Row],
    *,
    color: discord.Color = discord.Color.blue(),
    line_fn: LineFn = default_line,
    limit: int = 30,
) -> discord.Embed:
    """자동 보고용 비대화형 임베드 (비활성 순, 상위 limit명만)."""
    ordered = _sort_inactive(rows)
    shown = ordered[:limit]
    if shown:
        desc = "\n".join(line_fn(m, last) for m, last in shown)
        if len(ordered) > limit:
            desc += f"\n… 외 {len(ordered) - limit}명"
    else:
        desc = "해당하는 멤버가 없습니다. 🎉"
    embed = discord.Embed(title=title, description=desc, color=color)
    embed.set_footer(text=f"총 {len(ordered)}명")
    return embed


class MemberListView(discord.ui.View):
    def __init__(
        self,
        *,
        author_id: int,
        title: str,
        rows: list[Row],
        color: discord.Color = discord.Color.blurple(),
        line_fn: LineFn = default_line,
        per_page: int = PER_PAGE,
        timeout: float = 180,
    ) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.title = title
        self.color = color
        self.line_fn = line_fn
        self.per_page = per_page
        self._rows_src = rows
        self.sort_index = 0
        self.page = 0
        self.rows = SORT_MODES[0][2](rows)
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.rows) + self.per_page - 1) // self.per_page)

    def build_embed(self) -> discord.Embed:
        start = self.page * self.per_page
        chunk = self.rows[start : start + self.per_page]
        desc = (
            "\n".join(self.line_fn(m, last) for m, last in chunk)
            if chunk
            else "해당하는 멤버가 없습니다. 🎉"
        )
        embed = discord.Embed(title=self.title, description=desc, color=self.color)
        sort_name = SORT_MODES[self.sort_index][0]
        embed.set_footer(
            text=f"총 {len(self.rows)}명 · {self.page + 1}/{self.total_pages} 페이지 · 정렬: {sort_name}"
        )
        return embed

    def _sync_buttons(self) -> None:
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages - 1

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "이 메뉴는 명령어를 실행한 사람만 조작할 수 있습니다.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        await self._refresh(interaction)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self.total_pages - 1, self.page + 1)
        await self._refresh(interaction)

    @discord.ui.button(emoji="🔃", label="정렬: 비활성 순", style=discord.ButtonStyle.primary)
    async def sort_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.sort_index = (self.sort_index + 1) % len(SORT_MODES)
        name, _emoji, fn = SORT_MODES[self.sort_index]
        self.rows = fn(self._rows_src)
        self.page = 0
        button.label = f"정렬: {name}"
        await self._refresh(interaction)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
