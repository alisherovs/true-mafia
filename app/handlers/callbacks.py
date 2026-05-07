from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.game_engine import GameEngine
from app.texts import t

router = Router()


@router.callback_query(F.data.startswith("join:"))
async def join_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("Bad callback", show_alert=True)
        return

    game_id = int(parts[1])
    if callback.message.chat.type == "private":
        await callback.answer(t(await engine.get_user_language(callback.from_user.id), "group_only"), show_alert=True)
        return

    active = await engine.active_game_for_chat(callback.message.chat.id)
    if active is None or active.id != game_id:
        await callback.answer(t(await engine.get_group_language(callback.message.chat.id), "callback_expired"), show_alert=True)
        return

    ok, text = await engine.join_game(callback.bot, game_id, callback.from_user)
    await callback.answer(text, show_alert=not ok)


@router.callback_query(F.data.startswith("act:"))
async def action_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("Bad callback", show_alert=True)
        return

    _, action_key, game_id_raw, actor_id_raw, target_id_raw = parts
    try:
        game_id = int(game_id_raw)
        actor_id = int(actor_id_raw)
        target_id = int(target_id_raw)
    except ValueError:
        await callback.answer("Bad callback", show_alert=True)
        return

    if callback.from_user.id != actor_id:
        await callback.answer("Bu tugma siz uchun emas.", show_alert=True)
        return

    ok, text = await engine.record_action(callback.bot, game_id, actor_id, action_key, target_id)
    if ok and callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    await callback.answer(text, show_alert=not ok)


@router.callback_query(F.data.startswith("commissar:"))
async def commissar_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Bad callback", show_alert=True)
        return
    _, action_key, game_id_raw, actor_id_raw = parts
    try:
        game_id = int(game_id_raw)
        actor_id = int(actor_id_raw)
    except ValueError:
        await callback.answer("Bad callback", show_alert=True)
        return
    if callback.from_user.id != actor_id:
        await callback.answer("Bu tugma siz uchun emas.", show_alert=True)
        return

    ok, text, keyboard = await engine.commissar_targets_keyboard(game_id, actor_id, action_key)
    if not ok:
        await callback.answer(text, show_alert=True)
        return
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("vote:"))
async def vote_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Bad callback", show_alert=True)
        return

    _, game_id_raw, target_id_raw = parts
    try:
        game_id = int(game_id_raw)
        target_id = int(target_id_raw)
    except ValueError:
        await callback.answer("Bad callback", show_alert=True)
        return

    ok, text = await engine.cast_vote(callback.bot, game_id, callback.from_user.id, target_id)
    if ok and callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    await callback.answer(text, show_alert=not ok)


@router.callback_query(F.data.startswith("hang:"))
async def hang_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Bad callback", show_alert=True)
        return
    _, answer, game_id_raw, target_id_raw = parts
    try:
        game_id = int(game_id_raw)
        target_id = int(target_id_raw)
    except ValueError:
        await callback.answer("Bad callback", show_alert=True)
        return
    ok, text = await engine.confirm_hang(
        bot=callback.bot,
        game_id=game_id,
        target_id=target_id,
        confirmed=answer == "yes",
        voter_id=callback.from_user.id,
    )
    await callback.answer(text, show_alert=not ok)


@router.callback_query(F.data.startswith("judgecancel:"))
async def judge_cancel_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("Bad callback", show_alert=True)
        return
    _, game_id_raw, target_id_raw, judge_id_raw, confirm_message_id_raw = parts
    try:
        game_id = int(game_id_raw)
        target_id = int(target_id_raw)
        judge_id = int(judge_id_raw)
        confirm_message_id = int(confirm_message_id_raw)
    except ValueError:
        await callback.answer("Bad callback", show_alert=True)
        return
    if callback.from_user.id != judge_id:
        await callback.answer("Bu tugma siz uchun emas.", show_alert=True)
        return
    ok, text = await engine.judge_cancel_hang(
        bot=callback.bot,
        game_id=game_id,
        target_id=target_id,
        judge_id=judge_id,
        confirm_message_id=confirm_message_id,
    )
    if ok and callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    await callback.answer(text, show_alert=not ok)


@router.callback_query(F.data.startswith("settings:"))
async def settings_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if callback.message.chat.type == "private":
        await callback.answer("Group settings only", show_alert=True)
        return

    allowed = await engine.is_admin_or_creator(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        user_id=callback.from_user.id,
    )
    if not allowed:
        await callback.answer("Bu inline panel faqat adminlar uchun.", show_alert=True)
        return

    lang = await engine.get_group_language(callback.message.chat.id)
    action = callback.data.split(":", maxsplit=1)[1]

    if action == "lang":
        await callback.answer("/lang buyrug'idan foydalaning", show_alert=True)
    elif action == "timeout":
        group = await engine.group_settings(callback.message.chat.id)
        await callback.answer(
            f"Joriy timeout: {group.registration_timeout}s. O'zgartirish: /settimeout <sekund>",
            show_alert=True,
        )
    elif action == "minplayers":
        await engine.update_group_setting(callback.message.chat.id, "min_players", 4)
        await callback.answer("Minimum players: 4", show_alert=True)
    elif action == "roles":
        await callback.answer("Role settings keyingi relizda kengayadi.", show_alert=True)
    elif action == "premium":
        await callback.answer("Premium status: disabled", show_alert=True)
    elif action == "logs":
        await callback.answer("Game logs serverda yoziladi.", show_alert=True)
    elif action == "stop":
        game = await engine.active_game_for_chat(callback.message.chat.id)
        if not game:
            await callback.answer(t(lang, "no_active_game"), show_alert=True)
            return
        allowed = await engine.is_admin_or_creator(callback.bot, callback.message.chat.id, callback.from_user.id, game.creator_telegram_id)
        if not allowed:
            await callback.answer(t(lang, "no_permission"), show_alert=True)
            return
        ok, text = await engine.stop_game(callback.bot, game.id)
        await callback.answer(text, show_alert=not ok)
    elif action == "media":
        await callback.answer("Media sozlamalari .env orqali boshqariladi.", show_alert=True)
    else:
        await callback.answer("Unknown action", show_alert=True)
