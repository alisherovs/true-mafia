from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.game_engine import GameEngine
from app.texts import t

router = Router()


@router.message(Command("top"))
async def cmd_top(message: Message, command: CommandObject, engine: GameEngine) -> None:
    lang = await (engine.get_user_language(message.from_user.id) if message.chat.type == "private" else engine.get_group_language(message.chat.id))
    mode = (command.args or "").strip().lower()

    if mode == "group" and message.chat.type != "private":
        local_top = await engine.top_players_in_group(message.chat.id, limit=10)
        if not local_top:
            await message.reply("Bu guruhda hali yakunlangan o'yinlar yo'q.")
            return
        lines = ["🏆 <b>TOP Reyting (Guruh)</b>", ""]
        for idx, (name, wins, total) in enumerate(local_top, 1):
            lines.append(f"{idx}. {name} — 🎯 {wins} | 🎲 {total}")
        await message.reply("\n".join(lines))
        return

    top = await engine.top_players(limit=10)
    if not top:
        await message.reply(t(lang, "top_empty"))
        return

    lines = ["🏆 <b>TOP Reyting (Global)</b>", ""]
    for idx, user in enumerate(top, 1):
        lines.append(f"{idx}. {user.display_name} — 🎯 {user.wins} | 🎲 {user.total_games}")
    if message.chat.type != "private":
        lines.append("")
        lines.append("Guruh reytingi uchun: /top group")
    await message.reply("\n".join(lines))
