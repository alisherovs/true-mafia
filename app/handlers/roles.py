from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.game_engine import GameEngine
from app.keyboards import rules_back_keyboard, start_menu_keyboard
from app.texts import t

router = Router()


@router.message(Command("roles"))
async def cmd_roles(message: Message, engine: GameEngine) -> None:
    lang = await (engine.get_user_language(message.from_user.id) if message.chat.type == "private" else engine.get_group_language(message.chat.id))
    await message.answer(t(lang, "rules_text"))


@router.callback_query(F.data == "rules:show")
async def callback_rules(callback: CallbackQuery, engine: GameEngine) -> None:
    lang = await engine.get_user_language(callback.from_user.id)
    await callback.message.edit_text(t(lang, "rules_text"), reply_markup=rules_back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "start:back")
async def callback_start_back(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    lang = await engine.get_user_language(callback.from_user.id)
    await callback.message.edit_text(
        t(lang, "start_menu"),
        reply_markup=start_menu_keyboard(
            lang,
            settings,
            is_admin=callback.from_user.id in settings.admin_ids,
        ),
    )
    await callback.answer()
