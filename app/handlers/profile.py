from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.database import SessionLocal
from app.game_engine import GameEngine
from app.keyboards import commands_keyboard, profile_dashboard_keyboard
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
    "/lastwords matn - o'lim oldi so'zingiz\n"
    "/gun - guruhda reply qilib miltiq ishlatish\n\n"
    "<b>Iqtisod:</b>\n"
    "💵 Dollar olish - profildagi <b>Xarid qilish 💵</b> tugmasi orqali 💎 almazni 💵 dollarga almashtirish\n"
    "/give miqdor - guruhda sovg'a paneli ochish\n"
    "/give miqdor izoh - reply qilingan userga almaz berish\n"
    "/give user_id miqdor izoh - userga almaz berish"
)

GROUP_COMMANDS_TEXT = (
    "📋 <b>Guruh buyruqlari</b>\n\n"
    "/game - ro'yxatdan o'tishni boshlash\n"
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
    "/gsend miqdor - guruhni premium reytingga chiqarish uchun almaz yuborish\n"
    "/gun - reply qilingan o'yinchiga miltiq ishlatish"
)


async def _send_profile(message: Message, engine: GameEngine, settings: Settings) -> None:
    await engine.ensure_user(message.from_user)
    user = await engine.get_user(message.from_user.id)
    if user is None:
        return

    if message.chat.type == "private":
        in_running_game = await engine.user_in_running_game(message.from_user.id)
        reply_markup = None
        if not in_running_game:
            reply_markup = profile_dashboard_keyboard(
                settings,
                user=user,
                is_admin=message.from_user.id in settings.admin_ids,
                news_url=await engine.get_news_channel_url(),
            )
        await message.answer(engine.format_user_dashboard(user), reply_markup=reply_markup)
        return

    await message.answer(engine.format_user_dashboard(user))


@router.message(Command("profile"))
async def cmd_profile(message: Message, engine: GameEngine, settings: Settings) -> None:
    if message.from_user is None:
        return
    await _send_profile(message, engine, settings)


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
        reply_markup = profile_dashboard_keyboard(
            settings,
            user=user,
            is_admin=callback.from_user.id in settings.admin_ids,
            news_url=await engine.get_news_channel_url(),
        )
    await callback.message.edit_text(engine.format_user_dashboard(user), reply_markup=reply_markup)
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
        "use_gun",
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

    await callback.message.edit_text(
        engine.format_user_dashboard(user),
        reply_markup=profile_dashboard_keyboard(
            settings,
            user=user,
            is_admin=callback.from_user.id in settings.admin_ids,
            news_url=await engine.get_news_channel_url(),
        ),
    )
    await callback.answer("Sozlama yangilandi.")
