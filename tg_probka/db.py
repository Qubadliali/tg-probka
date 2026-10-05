# -*- coding: utf-8 -*-
"""
Работа с Turso (libsql) вместо локального SQLite.
Синтаксис SQL — тот же. Меняется только способ подключения.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import libsql_client

from tg_probka.config import settings
from tg_probka.domain import DailyLimitReached, NoAccounts, TopRow, User

log = logging.getLogger(__name__)


# ======================= connection =======================
_client: libsql_client.Client | None = None


def get_client() -> libsql_client.Client:
    """Ленивая инициализация клиента Turso."""
    global _client
    if _client is None:
        url = os.environ.get("TURSO_URL", "").strip()
        token = os.environ.get("TURSO_TOKEN", "").strip()
        if not url or not token:
            raise RuntimeError(
                "TURSO_URL и TURSO_TOKEN должны быть заданы в .env"
            )
        _client = libsql_client.create_client_sync(url=url, auth_token=token)
        log.info("Turso client создан для %s", url)
    return _client


@asynccontextmanager
async def connect():
    """Совместимость со старым API. Отдаёт async-обёртку над sync-клиентом."""
    client = get_client()
    yield _AsyncConnWrapper(client)


class _AsyncConnWrapper:
    """Обёртка, чтобы старый код `async with connect() as conn: await conn.execute(...)` работал."""

    def __init__(self, client: libsql_client.Client):
        self._client = client

    async def execute(self, sql: str, params: tuple | list | None = None):
        return self._client.execute(sql, params or [])

    async def executemany(self, sql: str, seq):
        for params in seq:
            self._client.execute(sql, params)

    async def executescript(self, script: str):
        # Turso/libsql не поддерживает executescript напрямую.
        # Разбиваем по ';' и выполняем по одной.
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                self._client.execute(stmt)

    @property
    def total_changes(self):
        return 0

    async def commit(self):
        pass

    async def rollback(self):
        pass


@asynccontextmanager
async def write_tx():
    """В Turso/libsql явные транзакции через sync-клиент ограничены.
    Для нашего бота достаточно выполнять запросы последовательно — SQLite в облаке сам атомарен
    для одиночных операторов."""
    client = get_client()
    yield _AsyncConnWrapper(client)


# ======================= migrations =======================
MIGRATIONS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id         INTEGER PRIMARY KEY,
        username        TEXT,
        lang            TEXT DEFAULT 'ru',
        last_drop_date  TEXT DEFAULT '',
        referrer_id     INTEGER DEFAULT 0,
        refs_count      INTEGER DEFAULT 0,
        bonus_accounts  INTEGER DEFAULT 0,
        ref_rewarded    INTEGER DEFAULT 0,
        is_banned       INTEGER DEFAULT 0,
        is_active       INTEGER DEFAULT 1,
        accounts_taken  INTEGER DEFAULT 0,
        role            TEXT DEFAULT 'user',
        created_at      TEXT DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS accounts (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        account_data TEXT NOT NULL UNIQUE,
        is_used      INTEGER DEFAULT 0,
        used_by      INTEGER DEFAULT 0,
        used_at      TEXT DEFAULT '',
        added_by     INTEGER DEFAULT 0,
        added_at     TEXT DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS channels (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_username TEXT UNIQUE NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ts        TEXT NOT NULL,
        actor_id  INTEGER,
        action    TEXT NOT NULL,
        target_id INTEGER,
        payload   TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_accounts_free ON accounts(is_used)",
    "CREATE INDEX IF NOT EXISTS idx_users_ref ON users(referrer_id)",
    "CREATE INDEX IF NOT EXISTS idx_users_taken ON users(accounts_taken)",
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC)",
]


async def migrate() -> None:
    client = get_client()
    for idx, script in enumerate(MIGRATIONS, start=1):
        try:
            client.execute(script)
        except Exception as e:
            # Если таблица уже есть — пропускаем
            log.debug("Migration %s skipped: %s", idx, e)
    log.info("Turso migrations applied")


# ======================= settings =======================
async def get_setting(key: str, default: str = "") -> str:
    client = get_client()
    result = client.execute("SELECT value FROM settings WHERE key = ?", [key])
    rows = result.rows
    return rows[0][0] if rows else default


async def set_setting(key: str, value: str) -> None:
    client = get_client()
    client.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [key, value],
    )


async def seed_defaults(defaults: dict[str, str]) -> None:
    client = get_client()
    for k, v in defaults.items():
        client.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", [k, v]
        )


# ======================= users =======================
def _row_to_user(row) -> User:
    """Преобразование строки Turso в объект User."""
    # libsql возвращает Row — индексируется как список
    data = dict(zip(
        ["user_id", "username", "lang", "last_drop_date", "referrer_id",
         "refs_count", "bonus_accounts", "ref_rewarded", "is_banned",
         "is_active", "accounts_taken", "role", "created_at"],
        row,
    ))
    return User.from_row(data)


async def get_user(user_id: int) -> User | None:
    client = get_client()
    result = client.execute("SELECT * FROM users WHERE user_id = ?", [user_id])
    if not result.rows:
        return None
    return _row_to_user(result.rows[0])


async def get_user_by_username(username: str) -> User | None:
    uname = (username or "").strip().lstrip("@").lower()
    if not uname:
        return None
    client = get_client()
    result = client.execute(
        "SELECT * FROM users WHERE LOWER(username) = ?", [uname]
    )
    if not result.rows:
        return None
    return _row_to_user(result.rows[0])


async def get_user_any(raw: str) -> User | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
        return await get_user(int(raw))
    return await get_user_by_username(raw)


async def upsert_user_on_start(
    user_id: int, username: str, referrer_id: int, owner_id: int
) -> User:
    client = get_client()
    result = client.execute("SELECT * FROM users WHERE user_id = ?", [user_id])
    row = result.rows[0] if result.rows else None

    if row is None:
        if referrer_id == user_id or referrer_id < 0:
            referrer_id = 0
        role = "owner" if user_id == owner_id else "user"
        client.execute(
            "INSERT OR IGNORE INTO users "
            "(user_id, username, referrer_id, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [user_id, username, referrer_id, role,
             datetime.now(timezone.utc).isoformat()],
        )
        return await get_user(user_id)

    # Обновляем username и role, если надо
    existing = _row_to_user(row)
    updates, params = [], []
    if existing.username != username:
        updates.append("username = ?")
        params.append(username)
    if user_id == owner_id and existing.role.value != "owner":
        updates.append("role = 'owner'")
    if updates:
        params.append(user_id)
        client.execute(
            f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?", params
        )
        return await get_user(user_id)
    return existing


async def set_lang(user_id: int, lang: str) -> None:
    client = get_client()
    client.execute("UPDATE users SET lang = ? WHERE user_id = ?", [lang, user_id])


async def set_role(user_id: int, role: str) -> None:
    client = get_client()
    client.execute("UPDATE users SET role = ? WHERE user_id = ?", [role, user_id])


async def set_banned(user_id: int, banned: bool) -> None:
    client = get_client()
    client.execute(
        "UPDATE users SET is_banned = ? WHERE user_id = ?",
        [1 if banned else 0, user_id],
    )


async def set_inactive(user_id: int) -> None:
    client = get_client()
    client.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", [user_id])


async def count_role(role: str) -> int:
    client = get_client()
    result = client.execute("SELECT COUNT(*) FROM users WHERE role = ?", [role])
    return result.rows[0][0] if result.rows else 0


async def count_users() -> int:
    client = get_client()
    result = client.execute("SELECT COUNT(*) FROM users")
    return result.rows[0][0] if result.rows else 0


async def team() -> dict[str, list[str]]:
    client = get_client()
    result = client.execute(
        "SELECT role, username FROM users "
        "WHERE role IN ('owner','chief','admin','vip') "
        "AND username IS NOT NULL AND username != ''"
    )
    out: dict[str, list[str]] = {"owner": [], "chief": [], "admin": [], "vip": []}
    for row in result.rows:
        role, username = row[0], row[1] or ""
        out.setdefault(role, []).append("@" + username.lstrip("@"))
    return out


async def active_user_ids() -> list[int]:
    client = get_client()
    result = client.execute(
        "SELECT user_id FROM users WHERE is_banned = 0 AND is_active = 1"
    )
    return [row[0] for row in result.rows]


# ======================= accounts =======================
async def stock_count() -> int:
    client = get_client()
    result = client.execute("SELECT COUNT(*) FROM accounts WHERE is_used = 0")
    return result.rows[0][0] if result.rows else 0


async def used_count() -> int:
    client = get_client()
    result = client.execute("SELECT COUNT(*) FROM accounts WHERE is_used = 1")
    return result.rows[0][0] if result.rows else 0


async def add_accounts(lines: list[str], added_by: int) -> tuple[int, int]:
    lines = [l.strip() for l in lines if l.strip()]
    if not lines:
        return 0, 0
    client = get_client()
    ts = datetime.now(timezone.utc).isoformat()
    added = 0
    for line in lines:
        try:
            client.execute(
                "INSERT OR IGNORE INTO accounts (account_data, added_by, added_at) "
                "VALUES (?, ?, ?)",
                [line, added_by, ts],
            )
            added += 1
        except Exception:
            pass
    return added, len(lines) - added


async def issue_account_row(user_id: int, today: str, daily_limit: int) -> str:
    client = get_client()

    # Проверка лимита
    result = client.execute(
        "SELECT last_drop_date, bonus_accounts, accounts_taken "
        "FROM users WHERE user_id = ?",
        [user_id],
    )
    if not result.rows:
        raise NoAccounts()
    row = result.rows[0]
    last_date, bonus, taken_today_raw = row[0] or "", row[1] or 0, row[2] or 0
    taken_today = taken_today_raw if last_date == today else 0

    if last_date == today and taken_today >= daily_limit and bonus <= 0:
        raise DailyLimitReached()

    # Свободный аккаунт
    result = client.execute(
        "SELECT id FROM accounts WHERE is_used = 0 ORDER BY id LIMIT 1"
    )
    if not result.rows:
        raise NoAccounts()
    acc_id = result.rows[0][0]

    ts = datetime.now(timezone.utc).isoformat()
    # Атомарная выдача — только если он ещё свободен
    result = client.execute(
        "UPDATE accounts SET is_used = 1, used_by = ?, used_at = ? "
        "WHERE id = ? AND is_used = 0 RETURNING account_data",
        [user_id, ts, acc_id],
    )
    if not result.rows:
        raise NoAccounts()
    data = result.rows[0][0]

    # Списываем лимит или бонус
    if last_date == today and taken_today >= daily_limit and bonus > 0:
        client.execute(
            "UPDATE users SET bonus_accounts = bonus_accounts - 1, "
            "accounts_taken = accounts_taken + 1 WHERE user_id = ?",
            [user_id],
        )
    elif last_date == today:
        client.execute(
            "UPDATE users SET accounts_taken = accounts_taken + 1 WHERE user_id = ?",
            [user_id],
        )
    else:
        client.execute(
            "UPDATE users SET last_drop_date = ?, accounts_taken = 1 WHERE user_id = ?",
            [today, user_id],
        )
    return data


# ======================= referrals =======================
async def reward_referrer_if_first(user_id: int) -> None:
    client = get_client()
    result = client.execute(
        "SELECT referrer_id, ref_rewarded FROM users WHERE user_id = ?", [user_id]
    )
    if not result.rows:
        return
    referrer_id, ref_rewarded = result.rows[0][0], result.rows[0][1]
    if referrer_id <= 0 or ref_rewarded:
        return
    client.execute(
        "UPDATE users SET ref_rewarded = 1 WHERE user_id = ?", [user_id]
    )
    client.execute(
        "UPDATE users SET refs_count = refs_count + 1, "
        "bonus_accounts = bonus_accounts + 1 WHERE user_id = ?",
        [referrer_id],
    )


# ======================= channels =======================
async def list_channels() -> list[str]:
    client = get_client()
    result = client.execute("SELECT channel_username FROM channels")
    return [row[0] for row in result.rows]


async def replace_channels(channels: list[str]) -> None:
    channels = [c.strip() for c in channels if c.strip()]
    client = get_client()
    client.execute("DELETE FROM channels")
    for c in channels:
        client.execute(
            "INSERT OR IGNORE INTO channels (channel_username) VALUES (?)", [c]
        )


# ======================= audit =======================
async def audit_log(
    actor_id: int, action: str, target_id: int | None = None, payload: dict | None = None
) -> None:
    client = get_client()
    client.execute(
        "INSERT INTO audit_log (ts, actor_id, action, target_id, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            datetime.now(timezone.utc).isoformat(),
            actor_id, action, target_id,
            json.dumps(payload, ensure_ascii=False) if payload else None,
        ],
    )


async def audit_recent(limit: int = 50) -> list[dict]:
    client = get_client()
    result = client.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", [limit]
    )
    keys = ["id", "ts", "actor_id", "action", "target_id", "payload"]
    return [dict(zip(keys, row)) for row in result.rows]


# ======================= leaderboard =======================
async def top_by_refs(limit: int = 10) -> list[TopRow]:
    client = get_client()
    result = client.execute(
        "SELECT user_id, username, refs_count AS val FROM users "
        "WHERE refs_count > 0 ORDER BY val DESC LIMIT ?",
        [limit],
    )
    return [TopRow(row[0], row[1] or "", row[2]) for row in result.rows]


async def top_by_taken(limit: int = 10) -> list[TopRow]:
    client = get_client()
    result = client.execute(
        "SELECT user_id, username, accounts_taken AS val FROM users "
        "WHERE accounts_taken > 0 ORDER BY val DESC LIMIT ?",
        [limit],
    )
    return [TopRow(row[0], row[1] or "", row[2]) for row in result.rows]


async def top_by_added(limit: int = 10) -> list[TopRow]:
    client = get_client()
    result = client.execute(
        "SELECT added_by AS uid, COUNT(*) AS val FROM accounts "
        "WHERE added_by != 0 GROUP BY added_by ORDER BY val DESC LIMIT ?",
        [limit],
    )
    out: list[TopRow] = []
    for row in result.rows:
        uid, val = row[0], row[1]
        r2 = client.execute("SELECT username FROM users WHERE user_id = ?", [uid])
        username = r2.rows[0][0] if r2.rows else ""
        out.append(TopRow(uid, username or "", val))
    return out