from __future__ import annotations

from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram import Router

from app.game_engine import GameEngine
from app.enums import GameStatus
from app.texts import t

router = Router()


async def _start_game_with_mode(
    message: Message,
    engine: GameEngine,
    mode: str | None = None,
    *,
    tournament: bool = False,
    regular: bool = False,
) -> None:
    if message.from_user is None:
        return
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "command_in_group"))
        return

    await engine.ensure_user(message.from_user)
    await engine.get_or_create_group(message.chat.id, message.chat.title or "Group")

    lang = await engine.get_group_language(message.chat.id)
    if not await engine.bot_is_admin(message.bot, message.chat.id):
        await message.answer(t(lang, "bot_not_admin"))
        return

    active_game = await engine.active_game_for_chat(message.chat.id)
    if mode and active_game is not None and active_game.status != "registration":
        await message.answer("🎮 Aktiv o'yin tugamaguncha mode almashtirib bo'lmaydi.")
        return
    
    if mode:
        ok, msg = await engine.update_group_setting(message.chat.id, "role_preset", mode)
        if not ok:
            await message.answer(msg)
            return

    ok, text = await engine.create_game_registration(
        bot=message.bot,
        chat_id=message.chat.id,
        chat_title=message.chat.title or "Group",
        creator_id=message.from_user.id,
        tournament=tournament,
        regular=regular,
        role_preset=mode,
    )
    if not ok:
        await message.answer(text)
    elif mode:
        await message.answer(f"✅ Mode tanlandi: <b>{mode}</b>")
    elif tournament:
        await message.answer("🏆 Turnir ro'yxatdan o'tishi boshlandi.")


@router.message(Command("game"))
async def cmd_game(message: Message, engine: GameEngine) -> None:
    if message.from_user and message.chat.type != "private":
        ok, err = await engine.check_command_permission(message.bot, message.chat.id, message.from_user.id, "game")
        if not ok:
            await message.reply(err)
            return
    await _start_game_with_mode(message, engine, regular=True)


@router.message(Command("turnir"))
async def cmd_tournament_game(message: Message, engine: GameEngine) -> None:
    if message.from_user and message.chat.type != "private":
        ok, err = await engine.check_command_permission(message.bot, message.chat.id, message.from_user.id, "game")
        if not ok:
            await message.reply(err)
            return
    await _start_game_with_mode(message, engine, tournament=True)


@router.message(Command("classic"))
async def cmd_classic_game(message: Message, engine: GameEngine) -> None:
    await _start_game_with_mode(message, engine, "classic")


@router.message(Command("super"))
async def cmd_super_game(message: Message, engine: GameEngine) -> None:
    await _start_game_with_mode(message, engine, "super")


@router.message(Command("mega"))
async def cmd_mega_game(message: Message, engine: GameEngine) -> None:
    await _start_game_with_mode(message, engine, "mega")


@router.message(Command("leave"))
async def cmd_leave(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "command_in_group"))
        return

    game = await engine.active_game_for_chat(message.chat.id)
    lang = await engine.get_group_language(message.chat.id)
    if game is None:
        await message.answer(t(lang, "no_active_game"))
        return

    ok, text = await engine.leave_game(message.bot, game.id, message.from_user.id)
    await message.answer(text)


@router.message(Command("extend"))
async def cmd_extend(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "command_in_group"))
        return

    game = await engine.active_game_for_chat(message.chat.id)
    lang = await engine.get_group_language(message.chat.id)
    if game is None:
        await message.answer(t(lang, "no_active_game"))
        return

    allowed = await engine.is_admin_or_creator(message.bot, message.chat.id, message.from_user.id, game.creator_telegram_id)
    if not allowed:
        await message.answer(t(lang, "no_permission"))
        return

    seconds = 30
    if command.args and command.args.strip().isdigit():
        parsed = int(command.args.strip())
        if parsed in {30, 60}:
            seconds = parsed

    ok, text = await engine.extend_registration(message.bot, game.id, seconds)
    await message.answer(text)


@router.message(Command("stop"))
async def cmd_stop(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "command_in_group"))
        return

    ok, err = await engine.check_command_permission(message.bot, message.chat.id, message.from_user.id, "stop")
    if not ok:
        await message.reply(err)
        return

    game = await engine.active_game_for_chat(message.chat.id)
    lang = await engine.get_group_language(message.chat.id)
    if game is None:
        await message.answer(t(lang, "no_active_game"))
        return

    ok, text = await engine.stop_game(message.bot, game.id)
    await message.answer(text)


@router.message(Command("teamgame"))
async def cmd_teamgame(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    lang = await (engine.get_user_language(message.from_user.id) if message.chat.type == "private" else engine.get_group_language(message.chat.id))
    await message.answer(t(lang, "teamgame_stub"))


@router.message(Command("lastwords"))
async def cmd_lastwords(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    text = (command.args or "").strip()
    if not text:
        await message.answer("Foydalanish: /lastwords Munavvara")
        return
    ok, response = await engine.set_last_words(message.from_user.id, text)
    await message.answer(response)


@router.message(Command("tep"))
async def cmd_tep(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "command_in_group"))
        return

    game = await engine.active_game_for_chat(message.chat.id)
    lang = await engine.get_group_language(message.chat.id)
    if game is None:
        await message.answer(t(lang, "no_active_game"))
        return
    if game.status != GameStatus.ACTIVE.value:
        await message.answer("Bu buyruq faqat davom etayotgan o'yinda ishlaydi.")
        return

    allowed = await engine.is_admin_or_creator(message.bot, message.chat.id, message.from_user.id, game.creator_telegram_id)
    if not allowed:
        await message.answer(t(lang, "no_permission"))
        return

    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.answer("Foydalanish: /tep <o'yinchi raqami>\nMasalan: /tep 1")
        return

    number = int(raw)
    ok, text = await engine.admin_remove_player_by_number(
        bot=message.bot,
        chat_id=message.chat.id,
        admin_id=message.from_user.id,
        player_number=number,
    )
    if not ok:
        await message.answer(text)
