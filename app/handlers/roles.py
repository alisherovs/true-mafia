from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.enums import Role
from app.game_engine import GameEngine
from app.keyboards import role_info_keyboard, roles_menu_keyboard, start_menu_keyboard
from app.roles import ROLE_META
from app.texts import t

router = Router()


def _roles_menu_text() -> str:
    return "🃏 <b>Rollar haqida ma'lumot</b>\n\nKerakli rolni tanlang:"


def _role_info_text(role: Role) -> str:
    meta = ROLE_META[role]
    return f"Siz - {meta.emoji} <b>{meta.title_uz}</b>siz!\n{meta.short_desc_uz}"


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


@router.message(Command("roles"))
async def cmd_roles(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    await message.answer(_roles_menu_text(), reply_markup=roles_menu_keyboard())


@router.callback_query(F.data == "rules:show")
async def callback_rules(callback: CallbackQuery, engine: GameEngine) -> None:
    await _safe_edit(callback, _roles_menu_text(), reply_markup=roles_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "roles:list")
async def callback_roles_list(callback: CallbackQuery) -> None:
    await _safe_edit(callback, _roles_menu_text(), reply_markup=roles_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("roles:info:"))
async def callback_role_info(callback: CallbackQuery) -> None:
    raw_role = callback.data.rsplit(":", maxsplit=1)[-1] if callback.data else ""
    try:
        role = Role(raw_role)
    except ValueError:
        await callback.answer("Rol topilmadi.", show_alert=True)
        return
    await _safe_edit(callback, _role_info_text(role), reply_markup=role_info_keyboard())
    await callback.answer()


@router.callback_query(F.data == "start:back")
async def callback_start_back(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    lang = await engine.get_user_language(callback.from_user.id)
    await _safe_edit(
        callback,
        t(lang, "start_menu"),
        reply_markup=start_menu_keyboard(
            lang,
            settings,
            is_admin=callback.from_user.id in settings.admin_ids,
            news_url=await engine.get_news_channel_url(),
        ),
    )
    await callback.answer()
