from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_probka import db, services
from tg_probka.config import settings
from tg_probka.domain import DailyLimitReached, NoAccounts, ROLE_NAMES, Role
from tg_probka.i18n import LANGS, btn_filter, t
from tg_probka.keyboards import lang_kb, main_kb, subscribe_kb, top_menu_kb
from tg_probka.utils import esc

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    username = message.from_user.username or ""
    referrer_id = 0
    parts = (message.text or "").split()
    if len(parts) > 1 and parts[1].isdigit():
        referrer_id = int(parts[1])

    user = await db.upsert_user_on_start(uid, username, referrer_id, settings.owner_id)
    await state.clear()
    if user.is_banned and uid != settings.owner_id:
        await message.answer("❌ Вы заблокированы.")
        return
    await message.answer(t(user.lang, "welcome"), reply_markup=lang_kb())


@router.callback_query(F.data.startswith("lang_"))
async def set_lang_cb(cb: CallbackQuery):
    lang = cb.data.split("_", 1)[1]
    if lang not in LANGS:
        await cb.answer(); return
    await db.set_lang(cb.from_user.id, lang)
    u = await db.get_user(cb.from_user.id)
    role = u.role if u else Role.USER
    await cb.message.answer(t(lang, "main_menu"), reply_markup=main_kb(lang, role))
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.answer()


@router.message(F.text.in_(btn_filter("btn_get")))
async def menu_get(message: Message, lang: str):
    channels = await db.list_channels()
    pairs = []
    for ch in channels:
        if ch.startswith("http"):
            pairs.append((ch, ch))
        else:
            title = ch if ch.startswith("@") else f"@{ch}"
            pairs.append((title, f"https://t.me/{title.lstrip('@')}"))
    ch_text = "\n".join(f"{i+1}️⃣ {esc(p[0])}" for i, p in enumerate(pairs))
    await message.answer(f"{t(lang, 'sub_text')}\n\n{ch_text}", reply_markup=subscribe_kb(lang, pairs))


@router.message(F.text.in_(btn_filter("btn_stock")))
async def menu_stock(message: Message, lang: str):
    await message.answer(t(lang, "stock_info").format(stock=await db.stock_count()))


@router.message(F.text.in_(btn_filter("btn_ref")))
async def menu_ref(message: Message, bot: Bot, user, lang: str):
    if user is None:
        await message.answer("❌ /start"); return
    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start={user.user_id}"
    await message.answer(t(lang, "ref_info").format(
        ref_link=esc(ref_link), refs=user.refs_count, bonus=user.bonus_accounts))


@router.message(F.text.in_(btn_filter("btn_profile")))
async def menu_profile(message: Message, user, lang: str):
    if user is None:
        await message.answer("❌ /start"); return
    await message.answer(t(lang, "profile_info").format(
        uid=user.user_id, role=ROLE_NAMES.get(user.role, user.role),
        taken=user.accounts_taken, refs=user.refs_count, bonus=user.bonus_accounts))


@router.message(F.text.in_(btn_filter("btn_team")))
async def menu_team(message: Message, user):
    team = await db.team()
    lines = ["👥 <b>Команда бота</b>\n"]
    if team["owner"]:
        lines.append("👑 <b>ВЛАДЕЛЕЦ</b>")
        lines += [f"   • {esc(u)}" for u in team["owner"]]
    if team["chief"]:
        lines.append("\n🛡 <b>ГЛАВНЫЕ АДМИНЫ</b>")
        lines += [f"   • {esc(u)}" for u in team["chief"]]
    if team["admin"]:
        lines.append(f"\n🛡 <b>АДМИНЫ ({len(team['admin'])})</b>")
        lines += [f"   • {esc(u)}" for u in team["admin"]]
    if team["vip"]:
        lines.append(f"\n⭐ <b>ВИПЫ ({len(team['vip'])})</b>")
        lines += [f"   • {esc(u)}" for u in team["vip"]]
    if user:
        lines.append(f"\n━━━━━━━━━━━━━━━\n🎯 <b>Твоя роль:</b> {ROLE_NAMES.get(user.role, user.role)}")
    await message.answer("\n".join(lines))


@router.callback_query(F.data == "check_sub")
async def check_sub_cb(cb: CallbackQuery, bot: Bot, lang: str):
    res = await services.check_subscriptions(bot, cb.from_user.id)
    if not res.ok:
        await cb.answer(t(lang, "not_sub"), show_alert=True); return
    await cb.answer(t(lang, "sub_ok"))
    await _deliver(cb.message, cb.from_user.id, lang, bot, edit=True)


async def _deliver(message: Message, user_id: int, lang: str, bot: Bot, edit: bool):
    try:
        await (message.edit_text if edit else message.answer)(t(lang, "wait"))
    except Exception:
        pass
    try:
        await bot.send_chat_action(user_id, "typing")
    except Exception:
        pass

    try:
        account = await services.issue_account(user_id)
    except DailyLimitReached:
        text = t(lang, "already_got")
    except NoAccounts:
        text = t(lang, "limit_reached")
    else:
        await db.reward_referrer_if_first(user_id)
        me = await bot.get_me()
        ref_link = f"https://t.me/{me.username}?start={user_id}"
        text = t(lang, "success_drop").format(account=esc(account), ref_link=esc(ref_link))

    try:
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
    except TelegramBadRequest:
        await message.answer(text)


# ---------- Топы ----------
@router.message(F.text.in_(btn_filter("btn_top")))
async def menu_top(message: Message, lang: str):
    await message.answer(t(lang, "top_title"), reply_markup=top_menu_kb(lang))


async def _render_top(cb: CallbackQuery, lang: str, title_key: str, rows):
    if not rows:
        text = f"<b>{t(lang, title_key)}</b>\n\n{t(lang, 'top_empty')}"
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"<b>{t(lang, title_key)}</b>\n"]
        for i, r in enumerate(rows):
            prefix = medals[i] if i < 3 else f"{i+1}."
            name = f"@{esc(r.username)}" if r.username else f"id{esc(r.uid)}"
            lines.append(f"{prefix} {name} — <b>{r.value}</b>")
        text = "\n".join(lines)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(lang, "top_back"), callback_data="top_menu")]
    ])
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "top_refs")
async def top_refs(cb: CallbackQuery, lang: str):
    await _render_top(cb, lang, "top_refs", await db.top_by_refs()); await cb.answer()


@router.callback_query(F.data == "top_taken")
async def top_taken(cb: CallbackQuery, lang: str):
    await _render_top(cb, lang, "top_taken", await db.top_by_taken()); await cb.answer()


@router.callback_query(F.data == "top_added")
async def top_added(cb: CallbackQuery, lang: str):
    await _render_top(cb, lang, "top_added", await db.top_by_added()); await cb.answer()


@router.callback_query(F.data == "top_menu")
async def top_menu(cb: CallbackQuery, lang: str):
    try:
        await cb.message.edit_text(t(lang, "top_title"), reply_markup=top_menu_kb(lang))
    except TelegramBadRequest:
        pass
    await cb.answer()


@router.callback_query(F.data == "top_close")
async def top_close(cb: CallbackQuery):
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.answer()