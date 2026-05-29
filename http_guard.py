"""Cloudflare 1015 자동 회피 가드.

호스트 IP 가 Cloudflare 에서 차단(1015)당하면 봇이 계속 요청을 보낼수록
차단이 더 길어진다. 이 모듈은:

1. **HTTP 후킹**: 모든 디스코드 HTTP 응답을 감시하다가 본문에 `error code: 1015`
   문자열이 보이면 전역 `GUARD` 를 트립(기본 1시간 송신 정지).
2. **부팅 단계 1015**: `bot.run()` 이 던지는 HTTPException 본문을 확인해
   1015 면 명확한 메시지를 출력하고 비정상 종료 → Wispbyte 의 60초 재시도 차단을 유도.
3. **코그별 게이트**: 채팅·로그·범프 등 fire-and-forget 송신은 `GUARD.is_paused()`
   체크 후 스킵해서 차단을 더 늘리지 않음.

차단 카운터가 식기 전에 다시 요청하면 차단이 누적되므로, **트립되면 무조건 1시간 침묵**
시키는 것이 가장 빨리 풀리는 방법이다(Cloudflare 1015 의 동작 특성).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import discord

log = logging.getLogger(__name__)

# 차단 감지 후 송신을 멈추는 기본 시간(초). 너무 짧으면 카운터가 식기 전에 또 두드림.
CLOUDFLARE_PAUSE_SECONDS = 3600.0
# Cloudflare 1015 응답 본문에 반드시 들어있는 마커
CLOUDFLARE_1015_MARKER = "error code: 1015"


class HttpGuard:
    """전역 송신 게이트(싱글톤). 1015 감지 시 일정 시간 모든 outbound 호출을 차단."""

    def __init__(self) -> None:
        self._paused_until: float = 0.0
        self._trips: int = 0

    @staticmethod
    def looks_like_1015(body: str | None) -> bool:
        """응답 본문 문자열에 Cloudflare 1015 마커가 있는지."""
        if not body:
            return False
        return CLOUDFLARE_1015_MARKER in body

    def trip(self, duration: float = CLOUDFLARE_PAUSE_SECONDS, reason: str = "") -> None:
        """가드 활성화. 이미 활성화돼 있으면 더 늦은 시각으로 갱신."""
        until = time.monotonic() + duration
        if until > self._paused_until:
            self._paused_until = until
            self._trips += 1
            log.warning(
                "Cloudflare 1015 감지 — outbound 호출을 %d초간 일시정지합니다 (누적 트립 %d회). 사유: %s",
                int(duration), self._trips, reason or "런타임 응답",
            )

    def is_paused(self) -> bool:
        return time.monotonic() < self._paused_until

    def remaining(self) -> float:
        return max(0.0, self._paused_until - time.monotonic())


GUARD = HttpGuard()


def install_http_hook(bot: Any) -> None:
    """`bot.http.request` 를 감싸 응답 본문에 1015 마커가 보이면 GUARD 를 트립.

    호출은 그대로 진행시켜 호출부의 예외 처리 흐름을 깨지 않는다(감지·기록만 수행).
    이 함수는 봇 객체가 만들어진 직후(아직 login 전) 호출하는 것이 좋다.
    """
    original = bot.http.request

    async def guarded_request(route, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return await original(route, **kwargs)
        except discord.HTTPException as exc:
            # discord-py 의 HTTPException.__str__ 은 응답 본문을 포함한다.
            if exc.status == 429 and HttpGuard.looks_like_1015(str(exc)):
                method = getattr(route, "method", "?")
                path = getattr(route, "path", "?")
                GUARD.trip(reason=f"{method} {path}")
            raise

    bot.http.request = guarded_request  # type: ignore[assignment]
