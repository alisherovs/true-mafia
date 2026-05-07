from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.game_engine import GameEngine
from app.keyboards import language_keyboard, start_menu_keyboard
from app.texts import t

router = Router()


@router.message(Command("lang"))
async def cmd_lang(message: Message, engine: GameEngine) -> None:
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "choose_language"), reply_markup=language_keyboard("user"))
        return

    is_admin = await engine.is_admin_or_creator(bot=message.bot, chat_id=message.chat.id, user_id=message.from_user.id)
    lang = await engine.get_group_language(message.chat.id)
    if not is_admin:
        await message.reply(t(lang, "no_permission"))
        return
    await message.reply(t(lang, "choose_language"), reply_markup=language_keyboard("group", message.chat.id))


@router.callback_query(F.data.startswith("lang:"))
async def lang_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Bad callback", show_alert=True)
        return

    _, scope, lang_code, raw_chat_id = parts
    if scope == "menu":
        await callback.answer()
        return

    if scope == "user":
        await engine.set_user_language(callback.from_user.id, lang_code)
        await callback.answer(t(lang_code, "language_saved"))
        if callback.message.chat.type == "private":
            await callback.message.edit_text(
                t(lang_code, "start_menu"),
                reply_markup=start_menu_keyboard(
                    lang_code,
                    settings,
                    is_admin=callback.from_user.id in settings.admin_ids,
                ),
            )
        return

    if scope == "group":
        chat_id = int(raw_chat_id)
        if callback.message.chat.id != chat_id:
            await callback.answer(t("uz", "callback_expired"), show_alert=True)
            return

        is_admin = await engine.is_admin_or_creator(
            bot=callback.bot,
            chat_id=chat_id,
            user_id=callback.from_user.id,
        )
        if not is_admin:
            await callback.answer(t(await engine.get_group_language(chat_id), "no_permission"), show_alert=True)
            return
        await engine.set_group_language(chat_id, lang_code)
        await callback.answer(t(lang_code, "language_changed_group"))
        await callback.message.edit_text(t(lang_code, "choose_language"), reply_markup=language_keyboard("group", chat_id))
        return

    await callback.answer("Unsupported", show_alert=True)
