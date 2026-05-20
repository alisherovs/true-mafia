from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.credit import CREDIT_AMOUNTS, CREDIT_DAYS, CreditService
from app.database import SessionLocal
from app.game_engine import GameEngine
from app.keyboards import (
    commands_keyboard,
    credit_confirm_keyboard,
    credit_days_keyboard,
    credit_menu_keyboard,
    profile_dashboard_keyboard,
)
from app.models import User
from sqlalchemy import select

router = Router()


PRIVATE_COMMANDS_TEXT = (
    "📋 <b>Buyruqlar</b>\n\n"
    "<b>User panel:</b>\n"
    "/start - asosiy menyu\n"
    "/profile - profilingiz\n"
    "/commands - barcha buyruqlar\n"
    "/roles - rollar haqida ma'lumot\n"
    "/lang - tilni o'zgartirish\n"
    "/top - TOP reyting\n\n"
    "<b>O'yin paytida:</b>\n"
    "/lastwords matn - o'lim oldi so'zingiz\n\n"
    "<b>Iqtisod:</b>\n"
    "<tg-emoji emoji-id=\"5375296873982604963\">💰</tg-emoji> Dollar olish - profildagi <b>Xarid qilish <tg-emoji emoji-id=\"5375296873982604963\">💰</tg-emoji></b> tugmasi orqali <tg-emoji emoji-id=\"5471952986970267163\">💎</tg-emoji> almazni <tg-emoji emoji-id=\"5375296873982604963\">💰</tg-emoji> dollarga almashtirish\n"
    "/give miqdor - guruhda sovg'a paneli ochish\n"
    "/give miqdor izoh - reply qilingan userga almaz berish\n"
    "/give user_id miqdor izoh - userga almaz berish"
)

GROUP_COMMANDS_TEXT = (
    "📋 <b>Guruh buyruqlari</b>\n\n"
    "/game - ro'yxatdan o'tishni boshlash\n"
    "/turnir - turnir ro'yxatdan o'tishini boshlash\n"
    "/classic - Classic mode bilan ro'yxatdan o'tishni boshlash\n"
    "/super - Super mode bilan ro'yxatdan o'tishni boshlash\n"
    "/mega - Mega mode bilan ro'yxatdan o'tishni boshlash\n"
    "/start - ro'yxatni yopib o'yinni boshlash\n"
    "/teamgame - turnir o'yinini boshlash\n"
    "/leave - o'yindan chiqish\n"
    "/extend - ro'yxatdan o'tish vaqtini uzaytirish\n"
    "/settings - sozlamalarni bot private chatida ochish\n"
    "/settimeout soniya - ro'yxat vaqtini sozlash\n"
    "/stop - aktiv o'yinni to'xtatish\n"
    "/top - TOP reyting\n"
    "/roles - rollar haqida ma'lumot\n"
    "/lang - guruh tilini o'zgartirish\n"
    "/profile - profilingiz\n"
    "/give miqdor - sovg'a paneli ochish\n"
    "/gsend miqdor - guruhni premium reytingga chiqarish uchun almaz yuborish"
)


async def _send_profile(message: Message, engine: GameEngine, settings: Settings) -> None:
    user = await engine.ensure_user(message.from_user)
    if user is None:
        return

    if message.chat.type == "private":
        in_running_game = await engine.user_in_running_game(message.from_user.id)
        reply_markup = None
        if not in_running_game:
            has_hero, news_url = await asyncio.gather(
                engine.user_has_hero(message.from_user.id),
                engine.get_news_channel_url(),
            )
            reply_markup = profile_dashboard_keyboard(
                settings,
                user=user,
                is_admin=message.from_user.id in settings.admin_ids,
                news_url=news_url,
                has_hero=has_hero,
            )
        await message.answer(**engine.format_user_dashboard_entities(user), reply_markup=reply_markup)
        return

    await message.answer(**engine.format_user_dashboard_entities(user))


@router.message(Command("profile"))
async def cmd_profile(message: Message, engine: GameEngine, settings: Settings) -> None:
    if message.from_user is None:
        return
    await _send_profile(message, engine, settings)


async def _resolve_you_target(message: Message, command: CommandObject, engine: GameEngine) -> User | None:
    if message.reply_to_message and message.reply_to_message.from_user:
        return await engine.ensure_user(message.reply_to_message.from_user)

    args = (command.args or "").strip()
    if args:
        target_id_raw = args.split(maxsplit=1)[0]
        if not target_id_raw.isdigit():
            await message.answer("Foydalanish: <code>/you</code>, reply qilib <code>/you</code> yoki <code>/you user_id</code>")
            return None
        user = await engine.get_user(int(target_id_raw))
        if user is None:
            await message.answer("❌ Bu ID bo'yicha profil topilmadi. User avval botda /start bosgan bo'lishi kerak.")
            return None
        return user

    if message.from_user is None:
        return None
    return await engine.ensure_user(message.from_user)


@router.message(Command("you"))
async def cmd_you(message: Message, command: CommandObject, engine: GameEngine, settings: Settings) -> None:
    if message.from_user is None or message.from_user.id not in settings.admin_ids:
        await message.answer("❌ Sizda bu buyruqni ishlatish huquqi yo'q.")
        return

    if message.chat.type == "private":
        user = await engine.ensure_user(message.from_user)
        await message.answer(**engine.format_user_dashboard_entities(user))
        return

    user = await _resolve_you_target(message, command, engine)
    if user is None:
        return
    await message.answer(**engine.format_user_dashboard_entities(user))


@router.message(Command("commands"))
async def cmd_commands(message: Message) -> None:
    if message.chat.type == "private":
        await message.answer(PRIVATE_COMMANDS_TEXT, reply_markup=commands_keyboard())
        return
    await message.answer(GROUP_COMMANDS_TEXT)


@router.callback_query(F.data == "commands:open")
async def commands_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.edit_text(PRIVATE_COMMANDS_TEXT, reply_markup=commands_keyboard())
    await callback.answer()


@router.callback_query(F.data == "profile:open")
async def profile_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    user = await engine.ensure_user(callback.from_user)
    in_running_game = await engine.user_in_running_game(callback.from_user.id)
    reply_markup = None
    if not in_running_game:
        has_hero, news_url = await asyncio.gather(
            engine.user_has_hero(callback.from_user.id),
            engine.get_news_channel_url(),
        )
        reply_markup = profile_dashboard_keyboard(
            settings,
            user=user,
            is_admin=callback.from_user.id in settings.admin_ids,
            news_url=news_url,
            has_hero=has_hero,
        )
    try:
        await callback.message.edit_text(**engine.format_user_dashboard_entities(user), reply_markup=reply_markup)
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("invtoggle:"))
async def inventory_toggle_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    field = callback.data.split(":", maxsplit=1)[1]
    allowed = {
        "use_protection",
        "use_killer_protection",
        "use_vote_protection",
        "use_miner_protection",
        "use_drug_protection",
        "use_mask",
        "use_fake_document",
    }
    if field not in allowed:
        await callback.answer("Noma'lum sozlama.", show_alert=True)
        return

    await engine.ensure_user(callback.from_user)
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
        ).scalar_one_or_none()
        if user is None:
            await callback.answer("Profil topilmadi.", show_alert=True)
            return
        current = getattr(user, field, True) is not False
        setattr(user, field, not current)
        await session.commit()
        await session.refresh(user)

    has_hero, news_url = await asyncio.gather(
        engine.user_has_hero(callback.from_user.id),
        engine.get_news_channel_url(),
    )
    try:
        await callback.message.edit_text(
            **engine.format_user_dashboard_entities(user),
            reply_markup=profile_dashboard_keyboard(
                settings,
                user=user,
                is_admin=callback.from_user.id in settings.admin_ids,
                news_url=news_url,
                has_hero=has_hero,
            ),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("Sozlama yangilandi.")


@router.callback_query(F.data == "credit:open")
async def credit_open_callback(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    credit = CreditService(SessionLocal)
    text, has_active = await credit.menu_text(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=credit_menu_keyboard(has_active))
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("credit:amount:"))
async def credit_amount_callback(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    try:
        amount = int((callback.data or "").split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Miqdor noto'g'ri.", show_alert=True)
        return
    if amount not in CREDIT_AMOUNTS:
        await callback.answer("Miqdor noto'g'ri.", show_alert=True)
        return
    credit = CreditService(SessionLocal)
    try:
        await callback.message.edit_text(credit.amount_text(amount), reply_markup=credit_days_keyboard(amount))
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("credit:days:"))
async def credit_days_callback(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    try:
        amount = int(parts[2])
        days = int(parts[3])
    except (IndexError, ValueError):
        await callback.answer("Tanlov noto'g'ri.", show_alert=True)
        return
    if amount not in CREDIT_AMOUNTS or days not in CREDIT_DAYS:
        await callback.answer("Tanlov noto'g'ri.", show_alert=True)
        return
    credit = CreditService(SessionLocal)
    try:
        await callback.message.edit_text(credit.confirm_text(amount, days), reply_markup=credit_confirm_keyboard(amount, days))
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("credit:take:"))
async def credit_take_callback(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    try:
        amount = int(parts[2])
        days = int(parts[3])
    except (IndexError, ValueError):
        await callback.answer("Tanlov noto'g'ri.", show_alert=True)
        return
    credit = CreditService(SessionLocal)
    ok, text = await credit.take_credit(callback.from_user, amount, days)
    if ok:
        menu_text, has_active = await credit.menu_text(callback.from_user.id)
        text = f"{text}\n\n{menu_text}"
    else:
        has_active = False
    try:
        await callback.message.edit_text(text, reply_markup=credit_menu_keyboard(has_active))
    except TelegramBadRequest:
        pass
    await callback.answer("Kredit berildi." if ok else "Kredit berilmadi.", show_alert=not ok)


@router.callback_query(F.data == "credit:repay")
async def credit_repay_callback(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    credit = CreditService(SessionLocal)
    ok, text = await credit.repay(callback.from_user.id)
    if ok:
        menu_text, has_active = await credit.menu_text(callback.from_user.id)
        text = f"{text}\n\n{menu_text}"
    else:
        has_active = True
    try:
        await callback.message.edit_text(text, reply_markup=credit_menu_keyboard(has_active))
    except TelegramBadRequest:
        pass
    await callback.answer("Kredit so'ndirildi." if ok else "So'ndirilmadi.", show_alert=not ok)
