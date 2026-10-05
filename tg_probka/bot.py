# -*- coding: utf-8 -*-
"""
Точка входа: запускает бота и HTTP-заглушку для Render.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

from tg_probka import services
from tg_probka.config import settings, setup_logging
from tg_probka.db import migrate, seed_defaults
from tg_probka.handlers import build_root_router
from tg_probka.middlewares import (
    BanMaintenanceMiddleware,
    ThrottlingMiddleware,
    UserContextMiddleware,
)

log = logging.getLogger(__name__)


DEFAULTS = {
    "maintenance": "0",
    "daily_limit": str(settings.daily_limit_default),
    "support_text": "💝 <b>Поддержать проект</b>\n\nРеквизиты уточняйте у владельца.",
    "coop_text": "🤝 <b>Сотрудничество</b>\n\nНапишите: @owner_username",
}


# ---------- HTTP-заглушка для Render ----------
async def _http_root(request: web.Request) -> web.Response:
    return web.Response(text="Bot is running")


async def _start_http_server(port: int) -> web.AppRunner:
    """Поднимает HTTP-сервер на $PORT, чтобы Render видел живой порт."""
    app = web.Application()
    app.router.add_get("/", _http_root)
    app.router.add_get("/health", _http_root)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    log.info("HTTP-заглушка запущена на порту %s", port)
    return runner


# ---------- main ----------
async def main() -> None:
    setup_logging()
    await migrate()
    await seed_defaults(DEFAULTS)

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    for observer in (dp.message, dp.callback_query):
        observer.middleware(UserContextMiddleware())
        observer.middleware(BanMaintenanceMiddleware())
        observer.middleware(ThrottlingMiddleware(0.4))

    dp.include_router(build_root_router())

    # HTTP-заглушка (нужна только на Render, локально можно отключить)
    http_runner: web.AppRunner | None = None
    port_str = os.environ.get("PORT")
    if port_str and port_str.isdigit():
        http_runner = await _start_http_server(int(port_str))
    else:
        log.info("PORT не задан — HTTP-заглушка выключена (локальный запуск)")

    # Грациозная остановка
    stop_event = asyncio.Event()
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
    except NotImplementedError:
        pass

    backup_task = asyncio.create_task(services.backup_loop())

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        backup_task.cancel()
        if http_runner:
            await http_runner.cleanup()
        await bot.session.close()
        log.info("Остановлено.")