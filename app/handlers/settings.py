from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.game_engine import GameEngine
from app.keyboards import settings_keyboard
from app.texts import t
from app.roles import role_preset_label, role_preset_max_players

router = Router()


@router.message(Command("settings"))
async def cmd_settings(message: Message, engine: GameEngine) -> None:
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "group_only"))
        return

    lang = await engine.get_group_language(message.chat.id)
    allowed = await engine.is_admin_or_creator(message.bot, message.chat.id, message.from_user.id)
    if not allowed:
        await message.reply(t(lang, "no_permission"))
        return

    group = await engine.group_settings(message.chat.id)
    text = (
        f"{t(lang, 'settings_title')}\n\n"
        f"⏳ Registration timeout: <b>{group.registration_timeout}</b> soniya\n"
        f"👥 Minimum players: <b>{group.min_players}</b>\n\n"
        f"🎭 Role preset: <b>{role_preset_label(group.role_preset)}</b> "
        f"({role_preset_max_players(group.role_preset)} gacha)\n\n"
        "Timeoutni o'zgartirish: /settimeout 150"
    )
    try:
        await message.bot.send_message(
            message.from_user.id,
            text,
            reply_markup=settings_keyboard(lang, message.chat.id),
        )
        await message.reply("⚙️ Sozlamalar bot private chatiga yuborildi.")
    except TelegramForbiddenError:
        await message.reply("⚠️ Sozlamalarni botda ochish uchun avval botga /start bosing.")


@router.message(Command("settimeout"))
async def cmd_settimeout(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "group_only"))
        return

    lang = await engine.get_group_language(message.chat.id)
    allowed = await engine.is_admin_or_creator(message.bot, message.chat.id, message.from_user.id)
    if not allowed:
        await message.reply(t(lang, "no_permission"))
        return

    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.reply("Foydalanish: /settimeout <sekund>\nMasalan: /settimeout 180")
        return

    seconds = int(arg)
    if seconds < 10:
        await message.reply("Minimal vaqt: 10 soniya.")
        return

    await engine.update_group_setting(message.chat.id, "registration_timeout", seconds)
    await message.reply(f"✅ Registration timeout yangilandi: <b>{seconds}</b> soniya.")
