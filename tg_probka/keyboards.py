from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from tg_probka.domain import Role, at_least
from tg_probka.i18n import t


def lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
             InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")],
            [InlineKeyboardButton(text="🇹🇷 Türkçe", callback_data="lang_tr"),
             InlineKeyboardButton(text="🇸🇦 العربية", callback_data="lang_ar")],
            [InlineKeyboardButton(text="🇪🇸 Español", callback_data="lang_es")],
        ]
    )


def main_kb(lang: str, role: Role) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=t(lang, "btn_get")), KeyboardButton(text=t(lang, "btn_stock"))],
        [KeyboardButton(text=t(lang, "btn_ref")), KeyboardButton(text=t(lang, "btn_top"))],
        [KeyboardButton(text=t(lang, "btn_profile")), KeyboardButton(text=t(lang, "btn_team"))],
        [KeyboardButton(text=t(lang, "btn_coop")), KeyboardButton(text=t(lang, "btn_support"))],
    ]
    if at_least(role, Role.VIP):
        rows.append([KeyboardButton(text=t(lang, "btn_admin"))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def subscribe_kb(lang: str, channels: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=title, url=url)] for title, url in channels]
    rows.append([InlineKeyboardButton(text=t(lang, "check_sub"), callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def top_menu_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "top_refs"), callback_data="top_refs")],
            [InlineKeyboardButton(text=t(lang, "top_taken"), callback_data="top_taken")],
            [InlineKeyboardButton(text=t(lang, "top_added"), callback_data="top_added")],
            [InlineKeyboardButton(text=t(lang, "top_back"), callback_data="top_close")],
        ]
    )


def admin_panel_kb(role: Role) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if at_least(role, Role.VIP):
        rows.append([InlineKeyboardButton(text="➕ Добавить аккаунты", callback_data="admin_add_acc")])

    if at_least(role, Role.ADMIN):
        rows.append([InlineKeyboardButton(text="🚫 Забанить", callback_data="admin_ban"),
                     InlineKeyboardButton(text="✅ Разбанить", callback_data="admin_unban")])
        rows.append([InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")])
        rows.append([InlineKeyboardButton(text="⭐ Назначить вип", callback_data="admin_add_vip"),
                     InlineKeyboardButton(text="❌ Снять вип", callback_data="admin_rem_vip")])

    if at_least(role, Role.CHIEF):
        rows.append([InlineKeyboardButton(text="🛡 Назначить админа", callback_data="admin_add_admin"),
                     InlineKeyboardButton(text="❌ Снять админа", callback_data="admin_rem_admin")])
        rows.append([InlineKeyboardButton(text="🏅 Назначить гл. админа", callback_data="admin_add_chief"),
                     InlineKeyboardButton(text="❌ Снять гл. админа", callback_data="admin_rem_chief")])

    if role is Role.OWNER:
        rows.append([InlineKeyboardButton(text="📢 Каналы", callback_data="admin_set_channels")])
        rows.append([InlineKeyboardButton(text="💝 Текст поддержки", callback_data="admin_set_support")])
        rows.append([InlineKeyboardButton(text="🤝 Текст сотрудничества", callback_data="admin_set_coop")])
        rows.append([InlineKeyboardButton(text="🎯 Дневной лимит", callback_data="admin_set_limit")])

    rows.append([InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")])
    rows.append([InlineKeyboardButton(text="📜 Audit", callback_data="admin_audit")])
    rows.append([InlineKeyboardButton(text="🔎 Найти юзера", callback_data="admin_lookup")])

    if role is Role.OWNER:
        rows.append([InlineKeyboardButton(text="😴 Спать", callback_data="admin_sleep"),
                     InlineKeyboardButton(text="▶️ Включить", callback_data="admin_wake")])

    rows.append([InlineKeyboardButton(text="🚪 Выйти", callback_data="admin_exit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)