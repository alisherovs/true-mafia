from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.game_engine import GameEngine
from app.texts import t

router = Router()


def _display_name(user: object) -> str:
    full_name = getattr(user, "full_name", None)
    username = getattr(user, "username", None)
    return full_name or (f"@{username}" if username else "User")


@router.message(Command("para"))
async def cmd_para(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "command_in_group"))
        return
    if message.reply_to_message is None or message.reply_to_message.from_user is None:
        await message.reply("💌 Para so'rovini yuborish uchun kimningdir xabariga reply qilib /para yozing.")
        return

    target = message.reply_to_message.from_user
    if target.is_bot:
        await message.reply("Bot bilan para bo'lib bo'lmaydi.")
        return
    if target.id == message.from_user.id:
        await message.reply("O'zingiz bilan para bo'la olmaysiz.")
        return

    await engine.ensure_user(message.from_user)
    await engine.ensure_user(target)
    ok, text, keyboard = await engine.create_couple_request(
        chat_id=message.chat.id,
        requester_id=message.from_user.id,
        requester_name=_display_name(message.from_user),
        target_id=target.id,
        target_name=_display_name(target),
    )
    await message.reply(text, reply_markup=keyboard if ok else None)


@router.message(Command("stats"))
async def cmd_stats(message: Message, engine: GameEngine) -> None:
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id) if message.from_user else "uz"
        await message.answer(t(lang, "command_in_group"))
        return
    await message.reply(await engine.couple_stats_text(message.chat.id))


@router.message(Command("unpara"))
async def cmd_unpara(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "command_in_group"))
        return
    ok, text = await engine.unpair_user(message.chat.id, message.from_user.id)
    await message.reply(text)


@router.callback_query(F.data.startswith("para:"))
async def para_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return
    _, action, chat_id_raw, requester_id_raw, target_id_raw = parts
    try:
        chat_id = int(chat_id_raw)
        requester_id = int(requester_id_raw)
        target_id = int(target_id_raw)
    except ValueError:
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return
    if action not in {"accept", "reject"}:
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return

    requester = await engine.get_user(requester_id)
    target = await engine.get_user(target_id)
    requester_name = requester.display_name if requester else f"ID:{requester_id}"
    target_name = target.display_name if target else _display_name(callback.from_user)
    ok, text = await engine.answer_couple_request(
        chat_id=chat_id,
        requester_id=requester_id,
        requester_name=requester_name,
        target_id=target_id,
        target_name=target_name,
        accepted=action == "accept",
        actor_id=callback.from_user.id,
    )
    if not ok:
        await callback.answer(text, show_alert=True)
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.answer(text)
    await callback.answer("Javob qabul qilindi.")
