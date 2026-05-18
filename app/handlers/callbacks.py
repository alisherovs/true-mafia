from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery

from app.game_engine import GameEngine
from app.keyboards import role_preset_keyboard, settings_keyboard
from app.roles import GAME_MODES, role_preset_label, role_preset_max_players
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
    show_alert = not ok
    if ok:
        try:
            await callback.bot.send_message(
                callback.from_user.id,
                "✅ Siz o'yinga muvaffaqiyatli ro'yxatdan o'tdingiz.",
                reply_markup=await engine.group_return_keyboard(callback.bot, callback.message.chat.id),
            )
            text = "✅ Siz o'yinga qo'shildingiz. Tasdiq xabari bot private chatiga yuborildi."
        except TelegramForbiddenError:
            text = f"{text}\n\nBotga o'tish tugmasini bosing va private chatni oching."
            show_alert = True
    await callback.answer(text, show_alert=show_alert)


@router.callback_query(F.data.startswith("jointeam:"))
async def join_team_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or parts[2] not in {"blue", "red"}:
        await callback.answer("Bad callback", show_alert=True)
        return

    game_id = int(parts[1])
    team_key = parts[2]
    if callback.message.chat.type == "private":
        await callback.answer(t(await engine.get_user_language(callback.from_user.id), "group_only"), show_alert=True)
        return

    active = await engine.active_game_for_chat(callback.message.chat.id)
    if active is None or active.id != game_id:
        await callback.answer(t(await engine.get_group_language(callback.message.chat.id), "callback_expired"), show_alert=True)
        return

    ok, text = await engine.join_game(callback.bot, game_id, callback.from_user, tournament_team=team_key)
    show_alert = not ok
    if ok:
        try:
            await callback.bot.send_message(
                callback.from_user.id,
                text,
                reply_markup=await engine.group_return_keyboard(callback.bot, callback.message.chat.id),
            )
            text = f"{text} Tasdiq xabari bot private chatiga yuborildi."
        except TelegramForbiddenError:
            text = f"{text}\n\nBotga o'tish tugmasini bosing va private chatni oching."
            show_alert = True
    await callback.answer(text, show_alert=show_alert)


@router.callback_query(F.data.startswith("role:"))
async def role_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        await callback.answer("Bad callback", show_alert=True)
        return

    ok, text = await engine.send_private_role_menu(
        bot=callback.bot,
        game_id=int(parts[1]),
        telegram_id=callback.from_user.id,
    )
    if ok:
        await callback.answer("Rol va tugmalar bot private chatiga yuborildi.", show_alert=True)
    else:
        await callback.answer(text, show_alert=True)


@router.callback_query(F.data.startswith("skip:"))
async def skip_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Bad callback", show_alert=True)
        return

    _, scope, game_id_raw, owner_id_raw = parts
    try:
        game_id = int(game_id_raw)
        owner_id = int(owner_id_raw)
    except ValueError:
        await callback.answer("Bad callback", show_alert=True)
        return

    if scope in {"night", "judge"} and callback.from_user.id != owner_id:
        await callback.answer("Bu tugma siz uchun emas.", show_alert=True)
        return

    ok, text = await engine.skip_choice(callback.bot, game_id, callback.from_user.id, scope)
    if ok and callback.message:
        if scope == "vote":
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except TelegramBadRequest:
                    pass
        elif scope != "hang":
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
    if ok:
        await callback.answer()
    else:
        await callback.answer(text, show_alert=True)


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


@router.callback_query(F.data.startswith("sorhang:"))
async def sorcerer_hang_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Bad callback", show_alert=True)
        return
    _, game_id_raw, sorcerer_id_raw, target_id_raw = parts
    try:
        game_id = int(game_id_raw)
        sorcerer_id = int(sorcerer_id_raw)
        target_id = int(target_id_raw)
    except ValueError:
        await callback.answer("Bad callback", show_alert=True)
        return
    if callback.from_user.id != sorcerer_id:
        await callback.answer("Bu tanlov faqat afsungar uchun.", show_alert=True)
        return

    ok, text = await engine.resolve_sorcerer_hang_revenge(
        bot=callback.bot,
        game_id=game_id,
        sorcerer_id=sorcerer_id,
        target_id=target_id,
    )
    if ok and callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    await callback.answer(text, show_alert=not ok)


@router.callback_query(F.data.startswith("sorjudge:"))
async def sorcerer_judgement_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 6:
        await callback.answer("Bad callback", show_alert=True)
        return
    _, game_id_raw, sorcerer_id_raw, attacker_id_raw, action = parts
    try:
        game_id = int(game_id_raw)
        sorcerer_id = int(sorcerer_id_raw)
        attacker_id = int(attacker_id_raw)
    except ValueError:
        await callback.answer("Bad callback", show_alert=True)
        return
    if callback.from_user.id != sorcerer_id:
        await callback.answer("Bu tanlov faqat Sehrgar uchun.", show_alert=True)
        return

    ok, text = await engine.resolve_sorcerer_judgement(
        bot=callback.bot,
        game_id=game_id,
        sorcerer_id=sorcerer_id,
        attacker_id=attacker_id,
        action=action,
    )
    if ok and callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
    await callback.answer(text, show_alert=not ok)


@router.callback_query(F.data.startswith("jokerpick:"))
async def joker_pick_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("Bad callback", show_alert=True)
        return
    _, game_id_raw, target_id_raw, actor_id_raw, card_raw = parts
    try:
        game_id = int(game_id_raw)
        target_id = int(target_id_raw)
        actor_id = int(actor_id_raw)
        card = int(card_raw)
    except ValueError:
        await callback.answer("Bad callback", show_alert=True)
        return
    if callback.from_user.id != target_id:
        await callback.answer("Bu tugma siz uchun emas.", show_alert=True)
        return
    ok, text = await engine.resolve_joker_card_pick(
        bot=callback.bot,
        game_id=game_id,
        target_id=target_id,
        actor_id=actor_id,
        picked_card=card,
    )
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

    if action_key == "menu":
        ok, text, keyboard = await engine.commissar_action_menu_keyboard(game_id, actor_id)
    else:
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
            await callback.message.delete()
        except TelegramBadRequest:
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
    ok, text, keyboard = await engine.confirm_hang(
        bot=callback.bot,
        game_id=game_id,
        target_id=target_id,
        confirmed=answer == "yes",
        voter_id=callback.from_user.id,
    )
    if ok and callback.message and keyboard:
        try:
            await callback.message.edit_reply_markup(reply_markup=keyboard)
        except TelegramBadRequest:
            pass
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

    payload = callback.data.split(":", maxsplit=1)[1]
    target_chat_id = callback.message.chat.id
    action = payload
    if callback.message.chat.type == "private":
        parts = payload.split(":", maxsplit=1)
        if len(parts) != 2 or not parts[0].lstrip("-").isdigit():
            await callback.answer("Group settings only", show_alert=True)
            return
        target_chat_id = int(parts[0])
        action = parts[1]
    elif payload.split(":", maxsplit=1)[0].lstrip("-").isdigit():
        parts = payload.split(":", maxsplit=1)
        target_chat_id = int(parts[0])
        action = parts[1] if len(parts) > 1 else ""

    if callback.message.chat.type == "private" and target_chat_id == callback.message.chat.id:
        await callback.answer("Group settings only", show_alert=True)
        return

    allowed = await engine.is_admin_or_creator(
        bot=callback.bot,
        chat_id=target_chat_id,
        user_id=callback.from_user.id,
    )
    if not allowed:
        await callback.answer("Bu inline panel faqat adminlar uchun.", show_alert=True)
        return

    lang = await engine.get_group_language(target_chat_id)

    if action == "lang":
        await callback.answer("/lang buyrug'idan foydalaning", show_alert=True)
    elif action == "timeout":
        group = await engine.group_settings(target_chat_id)
        await callback.answer(
            f"Joriy timeout: {group.registration_timeout}s. O'zgartirish: /settimeout <sekund>",
            show_alert=True,
        )
    elif action == "minplayers":
        ok, msg = await engine.update_group_setting(target_chat_id, "min_players", 4)
        await callback.answer(msg, show_alert=True)
    elif action == "roles":
        group = await engine.group_settings(target_chat_id)
        await callback.message.edit_text(
            engine.format_role_preset_settings(group),
            reply_markup=role_preset_keyboard(group.role_preset, target_chat_id),
        )
        await callback.answer()
    elif action.startswith("rolepreset:"):
        preset = action.split(":", maxsplit=1)[1]
        if preset not in GAME_MODES and preset not in {"black23", "extended35"}:
            await callback.answer("Noma'lum role preset.", show_alert=True)
            return
        
        ok, msg = await engine.update_group_setting(target_chat_id, "role_preset", preset)
        if not ok:
            await callback.answer(msg, show_alert=True)
            return
        
        group = await engine.group_settings(target_chat_id)
        await callback.message.edit_text(
            engine.format_role_preset_settings(group),
            reply_markup=role_preset_keyboard(group.role_preset, target_chat_id),
        )
        await callback.answer(msg)
    elif action == "back":
        group = await engine.group_settings(target_chat_id)
        welcome = await engine.welcome_settings(target_chat_id)
        welcome_status = "🟢 yoqilgan" if welcome["enabled"] == "1" else "🔴 o'chirilgan"
        text = (
            f"{t(lang, 'settings_title')}\n\n"
            f"⏳ Registration timeout: <b>{group.registration_timeout}</b> soniya\n"
            f"🌙 Night timeout: <b>{group.night_timeout}</b> soniya\n"
            f"☀️ Day discussion timeout: <b>{group.day_discussion_timeout}</b> soniya\n"
            f"🗳 Voting timeout: <b>{group.day_voting_timeout}</b> soniya\n"
            f"👥 Minimum players: <b>{group.min_players}</b>\n\n"
            f"👋 Salomlashuv: <b>{welcome_status}</b>\n\n"
            f"🎭 Role preset: <b>{role_preset_label(group.role_preset)}</b> "
            f"({role_preset_max_players(group.role_preset)} gacha)"
        )
        await callback.message.edit_text(text, reply_markup=settings_keyboard(lang, target_chat_id))
        await callback.answer()
    elif action == "premium":
        await callback.answer("Premium status: disabled", show_alert=True)
    elif action == "logs":
        await callback.answer("Game logs serverda yoziladi.", show_alert=True)
    elif action == "stop":
        game = await engine.active_game_for_chat(target_chat_id)
        if not game:
            await callback.answer(t(lang, "no_active_game"), show_alert=True)
            return
        allowed = await engine.is_admin_or_creator(callback.bot, target_chat_id, callback.from_user.id, game.creator_telegram_id)
        if not allowed:
            await callback.answer(t(lang, "no_permission"), show_alert=True)
            return
        ok, text = await engine.stop_game(callback.bot, game.id)
        await callback.answer(text, show_alert=not ok)
    elif action == "media":
        await callback.answer("Media sozlamalari .env orqali boshqariladi.", show_alert=True)
    else:
        await callback.answer("Unknown action", show_alert=True)
