from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from tg_probka import db, services
from tg_probka.config import settings
from tg_probka.domain import ROLE_NAMES, ROLE_ORDER, Role, at_least, can_touch
from tg_probka.i18n import btn_filter
from tg_probka.keyboards import admin_panel_kb
from tg_probka.utils import esc

router = Router()
log = logging.getLogger(__name__)


# ---------- стейты ----------
S_ADD_ACC = "s_add_acc"
S_ROLE_ASSIGN = "s_role_assign"
S_ROLE_REMOVE = "s_role_remove"
S_BAN = "s_ban"
S_UNBAN = "s_unban"
S_BROADCAST = "s_broadcast"
S_CHANNELS = "s_channels"
S_SUPPORT = "s_support"
S_COOP = "s_coop"
S_LIMIT = "s_limit"
S_LOOKUP = "s_lookup"


async def _render_panel(target: Message | CallbackQuery, role: Role):
    text = (
        f"👑 <b>Админ-панель</b>\n"
        f"🎯 Роль: {ROLE_NAMES.get(role, role)}\n\n"
        f"📦 Аккаунтов: <b>{await db.stock_count()}</b>\n"
        f"👥 Пользователей: <b>{await db.count_users()}</b>\n"
        f"📢 Каналы: <code>{', '.join(await db.list_channels())}</code>\n"
        f"🎯 Дневной лимит: <b>{await db.get_setting('daily_limit', '1')}</b>\n"
        f"😴 Тех. работы: {'ВКЛ' if await db.get_setting('maintenance', '0') == '1' else 'выкл'}"
    )
    kb = admin_panel_kb(role)
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest:
            pass
    else:
        await target.answer(text, reply_markup=kb)


@router.message(F.text.in_(btn_filter("btn_admin")))
async def admin_btn(message: Message, role: Role, state: FSMContext):
    if not at_least(role, Role.VIP):
        await message.answer("🚫 Нет доступа."); return
    await state.set_state("admin_menu")
    await _render_panel(message, role)


@router.callback_query(F.data == "admin_panel")
async def admin_panel_refresh(cb: CallbackQuery, role: Role):
    if not at_least(role, Role.VIP):
        await cb.answer("🚫", show_alert=True); return
    await _render_panel(cb, role); await cb.answer()


@router.callback_query(F.data == "admin_exit")
async def admin_exit(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cb.message.edit_text("🚪 Вы вышли из админ-панели.")
    except TelegramBadRequest:
        pass
    await cb.answer()


# ---------- stats / sleep / wake ----------
@router.callback_query(F.data == "admin_stats")
async def admin_stats(cb: CallbackQuery, role: Role):
    if not at_least(role, Role.VIP):
        await cb.answer("🚫", show_alert=True); return
    await cb.answer(
        f"📦 Свободно: {await db.stock_count()}\n"
        f"✅ Выдано: {await db.used_count()}\n"
        f"👥 Юзеров: {await db.count_users()}",
        show_alert=True,
    )


@router.callback_query(F.data == "admin_sleep")
async def admin_sleep(cb: CallbackQuery, user, role: Role):
    if role is not Role.OWNER:
        await cb.answer("🚫 Только владелец.", show_alert=True); return
    await db.set_setting("maintenance", "1")
    await db.audit_log(user.user_id, "maintenance_on")
    await cb.answer("😴 ВКЛ", show_alert=True)


@router.callback_query(F.data == "admin_wake")
async def admin_wake(cb: CallbackQuery, user, role: Role):
    if role is not Role.OWNER:
        await cb.answer("🚫 Только владелец.", show_alert=True); return
    await db.set_setting("maintenance", "0")
    await db.audit_log(user.user_id, "maintenance_off")
    await cb.answer("▶️ ВЫКЛ", show_alert=True)


# ---------- добавить акки ----------
@router.callback_query(F.data == "admin_add_acc")
async def add_acc_start(cb: CallbackQuery, role: Role, state: FSMContext):
    if not at_least(role, Role.VIP):
        await cb.answer("🚫", show_alert=True); return
    await state.set_state(S_ADD_ACC)
    await cb.message.edit_text("📥 Отправь строки или .txt (до 2 МБ).")
    await cb.answer()


@router.message(F.func(lambda m: True))
async def add_acc_msg(message: Message, state: FSMContext, bot: Bot, user, role: Role):
    if await state.get_state() != S_ADD_ACC:
        return
    if not at_least(role, Role.VIP):
        await state.clear(); return

    if message.document:
        doc = message.document
        if not doc.file_name or not doc.file_name.lower().endswith(".txt"):
            await message.answer("❌ Только .txt."); return
        if (doc.file_size or 0) > settings.max_txt_bytes:
            await message.answer("❌ Слишком большой файл."); return
        file = await bot.get_file(doc.file_id)
        buf = await bot.download_file(file.file_path)
        lines = buf.read().decode("utf-8", errors="ignore").splitlines()
    else:
        lines = (message.text or "").splitlines()

    added, skipped = await services.add_accounts(lines, user.user_id)
    await db.audit_log(user.user_id, "accounts_added", payload={"added": added})
    await state.clear()
    await message.answer(f"✅ Добавлено: {added}\n♻️ Пропущено: {skipped}")
    await services.maybe_stock_alert(bot)


# ---------- назначение ролей ----------
async def _do_assign(message: Message, state: FSMContext, actor, actor_role: Role,
                     new_role: Role, minimum: Role):
    if not at_least(actor_role, minimum):
        await message.answer("🚫 Нет доступа."); await state.clear(); return
    target = await db.get_user_any((message.text or "").strip())
    if not target:
        await message.answer("❌ Не найден. Отправь ID или @username."); return
    if target.user_id == settings.owner_id:
        await message.answer("🚫 Нельзя менять владельца."); return
    if target.user_id == actor.user_id:
        await message.answer("🚫 Нельзя менять свою роль."); return
    if not can_touch(actor_role, target.role):
        await message.answer("🚫 Нельзя трогать равного или старшего."); return
    if actor_role is not Role.OWNER and ROLE_ORDER[new_role] >= ROLE_ORDER[actor_role]:
        await message.answer("🚫 Нельзя выдать роль не ниже своей."); return
    if new_role is Role.CHIEF and (await db.count_role("chief")) >= settings.max_chiefs:
        await message.answer(f"❌ Уже {settings.max_chiefs} гл. админов."); return

    await db.set_role(target.user_id, new_role.value)
    await db.audit_log(actor.user_id, "role_assign", target_id=target.user_id,
                       payload={"role": new_role.value})
    await state.clear()
    await message.answer(f"✅ <code>{target.user_id}</code> теперь {ROLE_NAMES.get(new_role, new_role)}")


@router.callback_query(F.data == "admin_add_vip")
async def add_vip(cb: CallbackQuery, role: Role, state: FSMContext):
    if not at_least(role, Role.ADMIN):
        await cb.answer("🚫", show_alert=True); return
    await cb.message.edit_text("⭐ Отправь ID или @username нового випа:")
    await state.set_state(S_ROLE_ASSIGN)
    await state.update_data(new_role=Role.VIP.value, minimum=Role.ADMIN.value)
    await cb.answer()


@router.callback_query(F.data == "admin_add_admin")
async def add_admin(cb: CallbackQuery, role: Role, state: FSMContext):
    if not at_least(role, Role.CHIEF):
        await cb.answer("🚫", show_alert=True); return
    await cb.message.edit_text("🛡 Отправь ID или @username нового админа:")
    await state.set_state(S_ROLE_ASSIGN)
    await state.update_data(new_role=Role.ADMIN.value, minimum=Role.CHIEF.value)
    await cb.answer()


@router.callback_query(F.data == "admin_add_chief")
async def add_chief(cb: CallbackQuery, role: Role, state: FSMContext):
    if not at_least(role, Role.CHIEF):
        await cb.answer("🚫", show_alert=True); return
    if (await db.count_role("chief")) >= settings.max_chiefs:
        await cb.answer(f"❌ Уже {settings.max_chiefs}.", show_alert=True); return
    await cb.message.edit_text("🏅 Отправь ID или @username нового гл. админа:")
    await state.set_state(S_ROLE_ASSIGN)
    await state.update_data(new_role=Role.CHIEF.value, minimum=Role.CHIEF.value)
    await cb.answer()


@router.message(F.func(lambda m: True))
async def assign_msg(message: Message, state: FSMContext, user, role: Role):
    if await state.get_state() != S_ROLE_ASSIGN:
        return
    d = await state.get_data()
    await _do_assign(message, state, user, role,
                     Role(d.get("new_role", Role.VIP.value)),
                     Role(d.get("minimum", Role.ADMIN.value)))


# ---------- снятие ролей ----------
async def _do_remove(message: Message, state: FSMContext, actor, actor_role: Role,
                     role_to_remove: Role, minimum: Role):
    if not at_least(actor_role, minimum):
        await message.answer("🚫 Нет доступа."); await state.clear(); return
    target = await db.get_user_any((message.text or "").strip())
    if not target:
        await message.answer("❌ Не найден."); return
    if target.user_id == settings.owner_id:
        await message.answer("🚫 Нельзя снять владельца."); return
    if not can_touch(actor_role, target.role):
        await message.answer("🚫 Нельзя трогать равного или старшего."); return
    if target.role is not role_to_remove:
        await message.answer(f"❌ Сейчас: {ROLE_NAMES.get(target.role, target.role)}"); return
    await db.set_role(target.user_id, Role.USER.value)
    await db.audit_log(actor.user_id, "role_remove", target_id=target.user_id,
                       payload={"role": role_to_remove.value})
    await state.clear()
    await message.answer(f"✅ С <code>{target.user_id}</code> снята роль {ROLE_NAMES.get(role_to_remove, role_to_remove)}")


@router.callback_query(F.data == "admin_rem_vip")
async def rem_vip(cb: CallbackQuery, role: Role, state: FSMContext):
    if not at_least(role, Role.ADMIN):
        await cb.answer("🚫", show_alert=True); return
    await cb.message.edit_text("❌ ID или @username випа:")
    await state.set_state(S_ROLE_REMOVE)
    await state.update_data(role_to_remove=Role.VIP.value, minimum=Role.ADMIN.value)
    await cb.answer()


@router.callback_query(F.data == "admin_rem_admin")
async def rem_admin(cb: CallbackQuery, role: Role, state: FSMContext):
    if not at_least(role, Role.CHIEF):
        await cb.answer("🚫", show_alert=True); return
    await cb.message.edit_text("❌ ID или @username админа:")
    await state.set_state(S_ROLE_REMOVE)
    await state.update_data(role_to_remove=Role.ADMIN.value, minimum=Role.CHIEF.value)
    await cb.answer()


@router.callback_query(F.data == "admin_rem_chief")
async def rem_chief(cb: CallbackQuery, role: Role, state: FSMContext):
    if not at_least(role, Role.CHIEF):
        await cb.answer("🚫", show_alert=True); return
    await cb.message.edit_text("❌ ID или @username гл. админа:")
    await state.set_state(S_ROLE_REMOVE)
    await state.update_data(role_to_remove=Role.CHIEF.value, minimum=Role.CHIEF.value)
    await cb.answer()


@router.message(F.func(lambda m: True))
async def remove_msg(message: Message, state: FSMContext, user, role: Role):
    if await state.get_state() != S_ROLE_REMOVE:
        return
    d = await state.get_data()
    await _do_remove(message, state, user, role,
                     Role(d.get("role_to_remove", Role.VIP.value)),
                     Role(d.get("minimum", Role.ADMIN.value)))


# ---------- бан / разбан ----------
@router.callback_query(F.data == "admin_ban")
async def ban_start(cb: CallbackQuery, role: Role, state: FSMContext):
    if not at_least(role, Role.ADMIN):
        await cb.answer("🚫", show_alert=True); return
    await cb.message.edit_text("🚫 ID или @username для бана:")
    await state.set_state(S_BAN); await cb.answer()


@router.message(F.func(lambda m: True))
async def ban_do(message: Message, state: FSMContext, user, role: Role):
    if await state.get_state() != S_BAN:
        return
    if not at_least(role, Role.ADMIN):
        await state.clear(); return
    target = await db.get_user_any((message.text or "").strip())
    if not target:
        await message.answer("❌ Не найден."); return
    if target.user_id == settings.owner_id:
        await message.answer("🚫 Владельца нельзя."); return
    if not can_touch(role, target.role):
        await message.answer("🚫 Нельзя трогать равного или старшего."); return
    await db.set_banned(target.user_id, True)
    await db.audit_log(user.user_id, "ban", target_id=target.user_id)
    await state.clear()
    await message.answer(f"🚫 <code>{target.user_id}</code> забанен.")


@router.callback_query(F.data == "admin_unban")
async def unban_start(cb: CallbackQuery, role: Role, state: FSMContext):
    if not at_least(role, Role.ADMIN):
        await cb.answer("🚫", show_alert=True); return
    await cb.message.edit_text("✅ ID или @username для разбана:")
    await state.set_state(S_UNBAN); await cb.answer()


@router.message(F.func(lambda m: True))
async def unban_do(message: Message, state: FSMContext, user, role: Role):
    if await state.get_state() != S_UNBAN:
        return
    if not at_least(role, Role.ADMIN):
        await state.clear(); return
    target = await db.get_user_any((message.text or "").strip())
    if not target:
        await message.answer("❌ Не найден."); return
    await db.set_banned(target.user_id, False)
    await db.audit_log(user.user_id, "unban", target_id=target.user_id)
    await state.clear()
    await message.answer(f"✅ <code>{target.user_id}</code> разбанен.")


# ---------- рассылка ----------
@router.callback_query(F.data == "admin_broadcast")
async def bc_start(cb: CallbackQuery, role: Role, state: FSMContext):
    if not at_least(role, Role.ADMIN):
        await cb.answer("🚫", show_alert=True); return
    await cb.message.edit_text("📢 Текст (HTML). Отмена: /cancel")
    await state.set_state(S_BROADCAST); await cb.answer()


@router.message(F.func(lambda m: True), F.text == "/cancel")
async def bc_cancel(message: Message, state: FSMContext):
    if await state.get_state() == S_BROADCAST:
        await state.clear()
        await message.answer("❌ Отменено.")


@router.message(F.func(lambda m: True))
async def bc_do(message: Message, state: FSMContext, bot: Bot, user, role: Role):
    if await state.get_state() != S_BROADCAST:
        return
    if not at_least(role, Role.ADMIN):
        await state.clear(); return
    text = message.html_text or message.text or ""
    if not text.strip():
        await message.answer("❌ Пусто."); return

    ids = await db.active_user_ids()
    await state.clear()
    ok = fail = 0
    for uid in ids:
        try:
            await bot.send_message(uid, text); ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await bot.send_message(uid, text); ok += 1
            except Exception:
                fail += 1
        except TelegramForbiddenError:
            await db.set_inactive(uid); fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await db.audit_log(user.user_id, "broadcast", payload={"ok": ok, "fail": fail})
    await message.answer(f"📢 ✅ {ok} / ❌ {fail}")


# ---------- настройки (owner) ----------
def _owner(role: Role) -> bool:
    return role is Role.OWNER


@router.callback_query(F.data == "admin_set_channels")
async def set_ch(cb: CallbackQuery, role: Role, state: FSMContext):
    if not _owner(role):
        await cb.answer("🚫 Только владелец.", show_alert=True); return
    await cb.message.edit_text("📢 Каналы (по одному на строку):")
    await state.set_state(S_CHANNELS); await cb.answer()


@router.message(F.func(lambda m: True))
async def save_ch(message: Message, state: FSMContext, user, role: Role):
    if await state.get_state() != S_CHANNELS:
        return
    if not _owner(role):
        await state.clear(); return
    lines = [l.strip() for l in (message.text or "").splitlines() if l.strip()]
    if not lines:
        await message.answer("❌ Пусто."); return
    await db.replace_channels(lines)
    await db.audit_log(user.user_id, "channels_set", payload={"count": len(lines)})
    await state.clear()
    await message.answer(f"✅ {len(lines)} каналов.")


@router.callback_query(F.data == "admin_set_support")
async def set_sup(cb: CallbackQuery, role: Role, state: FSMContext):
    if not _owner(role):
        await cb.answer("🚫", show_alert=True); return
    await cb.message.edit_text("💝 Новый текст поддержки:")
    await state.set_state(S_SUPPORT); await cb.answer()


@router.message(F.func(lambda m: True))
async def save_sup(message: Message, state: FSMContext, user, role: Role):
    if await state.get_state() != S_SUPPORT:
        return
    if not _owner(role):
        await state.clear(); return
    await db.set_setting("support_text", message.html_text or message.text or "")
    await db.audit_log(user.user_id, "support_text_set")
    await state.clear(); await message.answer("✅")


@router.callback_query(F.data == "admin_set_coop")
async def set_coop(cb: CallbackQuery, role: Role, state: FSMContext):
    if not _owner(role):
        await cb.answer("🚫", show_alert=True); return
    await cb.message.edit_text("🤝 Новый текст сотрудничества:")
    await state.set_state(S_COOP); await cb.answer()


@router.message(F.func(lambda m: True))
async def save_coop(message: Message, state: FSMContext, user, role: Role):
    if await state.get_state() != S_COOP:
        return
    if not _owner(role):
        await state.clear(); return
    await db.set_setting("coop_text", message.html_text or message.text or "")
    await db.audit_log(user.user_id, "coop_text_set")
    await state.clear(); await message.answer("✅")


@router.callback_query(F.data == "admin_set_limit")
async def set_limit(cb: CallbackQuery, role: Role, state: FSMContext):
    if not _owner(role):
        await cb.answer("🚫", show_alert=True); return
    await cb.message.edit_text("🎯 Дневной лимит (1–100):")
    await state.set_state(S_LIMIT); await cb.answer()


@router.message(F.func(lambda m: True))
async def save_limit(message: Message, state: FSMContext, user, role: Role):
    if await state.get_state() != S_LIMIT:
        return
    if not _owner(role):
        await state.clear(); return
    try:
        v = int((message.text or "").strip())
        if not 1 <= v <= 100:
            raise ValueError
    except ValueError:
        await message.answer("❌ 1–100."); return
    await db.set_setting("daily_limit", str(v))
    await db.audit_log(user.user_id, "daily_limit_set", payload={"value": v})
    await state.clear()
    await message.answer(f"✅ {v}")


# ---------- audit / lookup ----------
@router.callback_query(F.data == "admin_audit")
async def show_audit(cb: CallbackQuery, role: Role):
    if not at_least(role, Role.ADMIN):
        await cb.answer("🚫", show_alert=True); return
    rows = await db.audit_recent(30)
    if not rows:
        await cb.answer("Пусто.", show_alert=True); return
    lines = [f"{r['ts'][:19]} | {r['actor_id']} | {r['action']} | {r.get('target_id') or ''}" for r in rows]
    await cb.message.answer("<pre>" + "\n".join(lines) + "</pre>")
    await cb.answer()


@router.callback_query(F.data == "admin_lookup")
async def lookup_start(cb: CallbackQuery, role: Role, state: FSMContext):
    if not at_least(role, Role.ADMIN):
        await cb.answer("🚫", show_alert=True); return
    await cb.message.edit_text("🔎 ID или @username:")
    await state.set_state(S_LOOKUP); await cb.answer()


@router.message(F.func(lambda m: True))
async def lookup_do(message: Message, state: FSMContext, role: Role):
    if await state.get_state() != S_LOOKUP:
        return
    if not at_least(role, Role.ADMIN):
        await state.clear(); return
    u = await db.get_user_any((message.text or "").strip())
    if not u:
        await message.answer("❌ Не найден."); return
    await message.answer(
        f"👤 <b>{u.user_id}</b>\n"
        f"🔗 @{esc(u.username) or '—'}\n"
        f"🎯 {ROLE_NAMES.get(u.role, u.role)}\n"
        f"🚫 Бан: {'да' if u.is_banned else 'нет'}\n"
        f"🎁 Взял: {u.accounts_taken}\n"
        f"👥 Рефов: {u.refs_count} (+{u.bonus_accounts})\n"
        f"📅 {u.created_at[:19]}"
    )
    await state.clear()