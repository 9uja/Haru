"""PaaS(Koyeb 등) 헬스체크용 최소 HTTP 서버.

디스코드 봇은 인바운드 HTTP가 없어, Web Service 로 배포하면 헬스체크 실패로
재시작/스케일다운될 수 있다. PORT 환경변수가 주어진 경우에만 간단한 200 응답
서버를 띄워 상시 가동을 돕는다. (로컬 실행 시 PORT 가 없으면 아무것도 하지 않음)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from aiohttp import web


async def start_health_server() -> Optional[web.AppRunner]:
    port = os.getenv("PORT")
    if not port:
        return None

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=int(port))
    await site.start()
    logging.info("헬스체크 서버 시작 (port=%s)", port)
    return runner
