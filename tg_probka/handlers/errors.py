from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import ErrorEvent

from tg_probka.config import settings

router = Router()
log = logging.getLogger(__name__)


@router.errors()
async def on_error(event: ErrorEvent):
    log.exception("Unhandled: %s", event.exception)
    try:
        await event.update.bot.send_message(
            settings.owner_id, f"⚠️ Ошибка: <code>{type(event.exception).__name__}</code>"
        )
    except Exception:
        pass
    return True