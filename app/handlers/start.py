from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.enums import GameStatus
from app.game_engine import GameEngine
from app.keyboards import language_keyboard, premium_groups_keyboard, start_menu_keyboard
from app.texts import t

router = Router()


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
        active_game = await engine.active_game_for_chat(message.chat.id)
        if active_game and active_game.status == GameStatus.REGISTRATION.value:
            allowed = await engine.is_admin_or_creator(
                bot=message.bot,
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                game_creator_id=active_game.creator_telegram_id,
            )
            if not allowed:
                await message.reply(t(lang, "no_permission"))
                return
            await message.reply("⏱ Ro'yxatdan o'tish qo'lda yakunlandi. O'yin boshlanmoqda...")
            await engine.close_registration(message.bot, active_game.id)
            return

        if active_game and active_game.status == GameStatus.ACTIVE.value:
            await message.reply("🎮 O'yin allaqachon boshlangan.")
            return

        await message.reply(
            "<b>🕵🏻 Mafia Bot guruh rejimi</b>\n\n"
            "Asosiy buyruqlar:\n"
            "/game - ro'yxatdan o'tishni boshlash\n"
            "/leave - ro'yxatdan chiqish\n"
            "/extend 30 yoki /extend 60 - vaqtni uzaytirish\n"
            "/stop - o'yinni to'xtatish\n"
            "/roles - qoidalar\n"
            "/settings - admin panel\n"
            "/top - reyting\n"
            "/profile - profil\n"
            "/lang - guruh tili"
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
    if callback.message:
        await callback.message.edit_text(text, reply_markup=premium_groups_keyboard(groups))
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
