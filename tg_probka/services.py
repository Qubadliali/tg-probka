from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from cryptography.fernet import Fernet, InvalidToken

from tg_probka import db
from tg_probka.config import settings

log = logging.getLogger(__name__)


# ======================= crypto =======================
_fernet: Fernet | None = None
_fernet_ready = False


def _f() -> Fernet | None:
    global _fernet, _fernet_ready
    if _fernet_ready:
        return _fernet
    _fernet_ready = True
    if not settings.fernet_key:
        return None
    try:
        _fernet = Fernet(settings.fernet_key.encode())
    except Exception as e:
        log.error("FERNET_KEY некорректен: %s", e)
        _fernet = None
    return _fernet


def encrypt(plain: str) -> str:
    f = _f()
    return f.encrypt(plain.encode()).decode() if f else plain


def decrypt(cipher: str) -> str:
    f = _f()
    if not f:
        return cipher
    try:
        return f.decrypt(cipher.encode()).decode()
    except InvalidToken:
        return cipher


# ======================= time =======================
def today_local() -> str:
    try:
        tz = ZoneInfo(settings.timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).strftime("%Y-%m-%d")


# ======================= accounts =======================
async def daily_limit() -> int:
    raw = await db.get_setting("daily_limit", str(settings.daily_limit_default))
    try:
        return max(1, int(raw))
    except ValueError:
        return settings.daily_limit_default


async def add_accounts(lines: list[str], added_by: int) -> tuple[int, int]:
    return await db.add_accounts([encrypt(l) for l in lines], added_by)


async def issue_account(user_id: int) -> str:
    data = await db.issue_account_row(user_id, today_local(), await daily_limit())
    return decrypt(data)


# ======================= subscriptions =======================
@dataclass(slots=True)
class SubResult:
    ok: bool
    missing: list[str]
    channel_errors: list[str]


_sub_cache: dict[int, tuple[float, SubResult]] = {}


async def check_subscriptions(bot: Bot, user_id: int) -> SubResult:
    now = time.time()
    cached = _sub_cache.get(user_id)
    if cached and now - cached[0] < settings.sub_cache_ttl_sec:
        return cached[1]

    channels = await db.list_channels()
    missing: list[str] = []
    errors: list[str] = []

    async def check_one(ch: str):
        try:
            m = await bot.get_chat_member(chat_id=ch, user_id=user_id)
            if m.status in ("left", "kicked"):
                missing.append(ch)
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            log.warning("Канал %s: %s", ch, e)
            errors.append(ch); missing.append(ch)
        except Exception as e:
            log.warning("Канал %s: %s", ch, e)
            errors.append(ch); missing.append(ch)

    if channels:
        await asyncio.gather(*(check_one(c) for c in channels))

    res = SubResult(ok=not missing, missing=missing, channel_errors=errors)
    _sub_cache[user_id] = (now, res)
    return res


# ======================= backup =======================
async def _make_backup() -> None:
    os.makedirs(settings.backup_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(settings.backup_dir, f"db_{ts}.sqlite")
    try:
        await asyncio.to_thread(shutil.copyfile, settings.db_path, dst)
    except FileNotFoundError:
        return
    except Exception as e:
        log.error("Бэкап: %s", e); return

    try:
        files = sorted(
            (os.path.join(settings.backup_dir, f)
             for f in os.listdir(settings.backup_dir) if f.startswith("db_")),
            key=os.path.getmtime,
        )
        for old in files[:-12]:
            os.remove(old)
    except Exception as e:
        log.warning("Ротация бэкапов: %s", e)


async def backup_loop() -> None:
    while True:
        await _make_backup()
        await asyncio.sleep(settings.backup_interval_sec)


# ======================= stock alert =======================
_last_alerted = False


async def maybe_stock_alert(bot: Bot) -> None:
    global _last_alerted
    cnt = await db.stock_count()
    if cnt <= settings.low_stock_threshold and not _last_alerted:
        _last_alerted = True
        try:
            await bot.send_message(
                settings.owner_id, f"⚠️ Аккаунтов в запасе мало: <b>{cnt}</b>"
            )
        except Exception as e:
            log.warning("stock alert: %s", e)
    elif cnt > settings.low_stock_threshold:
        _last_alerted = False