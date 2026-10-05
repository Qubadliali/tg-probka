from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from tg_probka import db
from tg_probka.config import settings
from tg_probka.domain import Role, at_least


class UserContextMiddleware(BaseMiddleware):
    """Кладёт в data['user'], data['role'], data['lang']."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable[Any]],
        event: TelegramObject,
        data: dict,
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            data["user"] = None
            data["role"] = Role.USER
            data["lang"] = "ru"
            return await handler(event, data)

        user = await db.get_user(tg_user.id)
        data["user"] = user
        data["role"] = user.role if user else Role.USER
        data["lang"] = user.lang if user else "ru"
        return await handler(event, data)


class BanMaintenanceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable[Any]],
        event: TelegramObject,
        data: dict,
    ) -> Any:
        user = data.get("user")
        if user and user.user_id == settings.owner_id:
            return await handler(event, data)

        if user and user.is_banned:
            await self._reply(event, "❌ Вы заблокированы.")
            return None

        if (await db.get_setting("maintenance", "0")) == "1":
            await self._reply(event, "😴 Бот временно отключён.")
            return None

        return await handler(event, data)

    @staticmethod
    async def _reply(event: TelegramObject, text: str) -> None:
        try:
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
        except Exception:
            pass


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate: float = 0.4) -> None:
        self.rate = rate
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable[Any]],
        event: TelegramObject,
        data: dict,
    ) -> Any:
        u = data.get("event_from_user")
        if u is None or u.id == settings.owner_id:
            return await handler(event, data)

        now = time.monotonic()
        if now - self._last.get(u.id, 0.0) < self.rate:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except Exception:
                    pass
            return None
        self._last[u.id] = now
        return await handler(event, data)


class RoleFilter(BaseMiddleware):
    """Пускает только роли не ниже minimum."""

    def __init__(self, minimum: Role) -> None:
        self.minimum = minimum

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable[Any]],
        event: TelegramObject,
        data: dict,
    ) -> Any:
        role = data.get("role", Role.USER)
        if not at_least(role, self.minimum):
            try:
                if isinstance(event, Message):
                    await event.answer("🚫 Недостаточно прав.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 Недостаточно прав.", show_alert=True)
            except Exception:
                pass
            return None
        return await handler(event, data)