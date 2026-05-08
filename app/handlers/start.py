from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.enums import GameStatus
from app.game_engine import GameEngine
from app.keyboards import language_keyboard, premium_groups_keyboard, profile_dashboard_keyboard, start_menu_keyboard
from app.texts import t

router = Router()


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    engine: GameEngine,
    settings: Settings,
) -> None:
    if message.from_user is None:
        return
    if message.chat.type != "private":
        lang = await engine.get_group_language(message.chat.id)
        game = await engine.active_game_for_chat(message.chat.id)
        if game is None:
            await message.reply(t(lang, "group_start_no_game"))
            return
        if game.status == GameStatus.REGISTRATION.value:
            if not await engine.bot_is_admin(message.bot, message.chat.id):
                await message.reply(t(lang, "bot_not_admin"))
                return
            await engine.close_registration(message.bot, game.id)
            return
        if game.status == GameStatus.ACTIVE.value:
            await message.reply(t(lang, "group_start_game_active"))
            return
        await message.reply(
            "<b>🎮 O'yinni boshlash uchun /game buyrug'ini yuboring.</b>\n\n"
            "Ro'yxatdan o'tish boshlangach, pastdagi tugma orqali o'yinga qo'shilasiz."
        )
        return

    payload = (command.args or "").strip()
    if payload.startswith("join_"):
        parts = payload.split("_", maxsplit=2)
        if len(parts) == 3 and parts[1].isdigit():
            game_id = int(parts[1])
            try:
                chat_id = int(parts[2])
            except ValueError:
                chat_id = 0
            if chat_id != 0:
                ok, text = await engine.join_game_by_deeplink(
                    bot=message.bot,
                    game_id=game_id,
                    chat_id=chat_id,
                    tg_user=message.from_user,
                )
                if ok:
                    await message.answer(
                        "✅ Siz o'yinga muvaffaqiyatli ro'yxatdan o'tdingiz.",
                        reply_markup=await engine.group_return_keyboard(message.bot, chat_id),
                    )
                else:
                    await message.answer(
                        text,
                        reply_markup=await engine.group_return_keyboard(message.bot, chat_id),
                    )
                return
    if payload.startswith("vote_"):
        parts = payload.split("_", maxsplit=1)
        if len(parts) == 2 and parts[1].isdigit():
            ok, text = await engine.send_private_vote_menu(
                bot=message.bot,
                game_id=int(parts[1]),
                voter_id=message.from_user.id,
            )
            if not ok:
                await message.answer(text)
            return

    if payload.startswith("role_"):
        parts = payload.split("_", maxsplit=1)
        if len(parts) == 2 and parts[1].isdigit():
            ok, text = await engine.send_private_role_menu(
                bot=message.bot,
                game_id=int(parts[1]),
                telegram_id=message.from_user.id,
            )
            if not ok:
                await message.answer(text)
            return

    if payload == "profile":
        user = await engine.ensure_user(message.from_user)
        if await engine.user_in_running_game(message.from_user.id):
            await message.answer(
                "🎮 <b>Siz hozir aktiv o'yindasiz.</b>\n\n"
                "Profil statistikasi o'yin davomida ko'rsatilmaydi."
            )
            return
        await message.answer(
            engine.format_user_dashboard(user),
            reply_markup=profile_dashboard_keyboard(settings, user=user, is_admin=message.from_user.id in settings.admin_ids),
        )
        return

    existing = await engine.get_user(message.from_user.id)
    user = await engine.ensure_user(message.from_user)

    if existing is None or not user.language_selected:
        await message.answer(t("ru", "choose_language"), reply_markup=language_keyboard("user"))
        return

    lang = user.language or settings.default_language
    await message.answer(
        t(lang, "start_menu"),
        reply_markup=start_menu_keyboard(lang, settings, is_admin=message.from_user.id in settings.admin_ids),
    )


@router.callback_query(F.data == "lang:menu:user:0")
async def open_lang_from_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(t("ru", "choose_language"), reply_markup=language_keyboard("user"))
    await callback.answer()


@router.callback_query(F.data == "premium:info")
async def premium_info(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    groups = await engine.premium_groups()
    text = await engine.premium_groups_text()
    await _safe_edit(callback, text, reply_markup=premium_groups_keyboard(groups))
    await callback.answer()


@router.callback_query(F.data == "start:back")
async def back_to_start(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    """Go back to start menu."""
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    
    user = await engine.ensure_user(callback.from_user)
    lang = user.language or settings.default_language
    
    await callback.message.edit_text(
        t(lang, "start_menu"),
        reply_markup=start_menu_keyboard(lang, settings, is_admin=callback.from_user.id in settings.admin_ids),
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
