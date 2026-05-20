from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.game_engine import GameEngine

router = Router()


@router.message(Command("top"))
async def cmd_top(message: Message, engine: GameEngine) -> None:
    chat_id = message.chat.id if message.chat.type != "private" else None
    await message.reply(await engine.weekly_activity_top_text(message.bot, chat_id=chat_id))
