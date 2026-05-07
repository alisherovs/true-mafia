from __future__ import annotations

from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram import Router

from app.game_engine import GameEngine
from app.texts import t

router = Router()


@router.message(Command("game"))
async def cmd_game(message: Message, engine: GameEngine) -> None:
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
        await message.reply(t(lang, "bot_not_admin"))
        return

    ok, text = await engine.create_game_registration(
        bot=message.bot,
        chat_id=message.chat.id,
        chat_title=message.chat.title or "Group",
        creator_id=message.from_user.id,
    )
    await message.reply(text)


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
        await message.reply(t(lang, "no_active_game"))
        return

    ok, text = await engine.leave_game(message.bot, game.id, message.from_user.id)
    await message.reply(text)


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
        await message.reply(t(lang, "no_active_game"))
        return

    allowed = await engine.is_admin_or_creator(message.bot, message.chat.id, message.from_user.id, game.creator_telegram_id)
    if not allowed:
        await message.reply(t(lang, "no_permission"))
        return

    seconds = 30
    if command.args and command.args.strip().isdigit():
        parsed = int(command.args.strip())
        if parsed in {30, 60}:
            seconds = parsed

    ok, text = await engine.extend_registration(message.bot, game.id, seconds)
    await message.reply(text)


@router.message(Command("stop"))
async def cmd_stop(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "command_in_group"))
        return

    game = await engine.active_game_for_chat(message.chat.id)
    lang = await engine.get_group_language(message.chat.id)
    if game is None:
        await message.reply(t(lang, "no_active_game"))
        return

    allowed = await engine.is_admin_or_creator(message.bot, message.chat.id, message.from_user.id, game.creator_telegram_id)
    if not allowed:
        await message.reply(t(lang, "no_permission"))
        return

    ok, text = await engine.stop_game(message.bot, game.id)
    await message.reply(text)


@router.message(Command("teamgame"))
async def cmd_teamgame(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    lang = await (engine.get_user_language(message.from_user.id) if message.chat.type == "private" else engine.get_group_language(message.chat.id))
    await message.reply(t(lang, "teamgame_stub"))


@router.message(Command("lastwords"))
async def cmd_lastwords(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    text = (command.args or "").strip()
    if not text:
        await message.reply("Foydalanish: /lastwords Munavvara")
        return
    ok, response = await engine.set_last_words(message.from_user.id, text)
    await message.reply(response)
