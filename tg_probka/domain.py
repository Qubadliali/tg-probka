from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    USER = "user"
    VIP = "vip"
    ADMIN = "admin"
    CHIEF = "chief"
    OWNER = "owner"


ROLE_ORDER: dict[Role, int] = {
    Role.USER: 0, Role.VIP: 1, Role.ADMIN: 2, Role.CHIEF: 3, Role.OWNER: 4,
}

ROLE_NAMES: dict[Role, str] = {
    Role.OWNER: "👑 Владелец",
    Role.CHIEF: "🛡 Гл. админ",
    Role.ADMIN: "🛡 Админ",
    Role.VIP: "⭐ Вип",
    Role.USER: "👤 Юзер",
}


def at_least(role: Role, minimum: Role) -> bool:
    return ROLE_ORDER[role] >= ROLE_ORDER[minimum]


def can_touch(actor: Role, target: Role) -> bool:
    if actor is Role.OWNER:
        return True
    return ROLE_ORDER[actor] > ROLE_ORDER[target]


def parse_role(raw: str) -> Role:
    try:
        return Role(raw)
    except ValueError:
        return Role.USER


@dataclass(slots=True)
class User:
    user_id: int
    username: str
    lang: str
    last_drop_date: str
    referrer_id: int
    refs_count: int
    bonus_accounts: int
    ref_rewarded: bool
    is_banned: bool
    is_active: bool
    accounts_taken: int
    role: Role
    created_at: str

    @classmethod
    def from_row(cls, row) -> "User":
        return cls(
            user_id=row["user_id"],
            username=row["username"] or "",
            lang=row["lang"] or "ru",
            last_drop_date=row["last_drop_date"] or "",
            referrer_id=row["referrer_id"] or 0,
            refs_count=row["refs_count"] or 0,
            bonus_accounts=row["bonus_accounts"] or 0,
            ref_rewarded=bool(row["ref_rewarded"]),
            is_banned=bool(row["is_banned"]),
            is_active=bool(row["is_active"]),
            accounts_taken=row["accounts_taken"] or 0,
            role=parse_role(row["role"] or "user"),
            created_at=row["created_at"] or "",
        )


@dataclass(slots=True)
class TopRow:
    uid: int
    username: str
    value: int


class BotError(Exception): ...
class NoAccounts(BotError): ...
class DailyLimitReached(BotError): ...
class PermissionDenied(BotError): ...