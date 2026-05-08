from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, BotCommandScopeDefault
from aiogram.types import Message

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.game_engine import GameEngine
from app.handlers import admin, callbacks, economy, game, language, profile, roles, settings as settings_handler, start, top
from app.scheduler import scheduler, shutdown_scheduler, start_scheduler


class DeleteGroupCommandMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        result = await handler(event, data)
        if event.chat.type != "private" and event.text and event.text.startswith("/"):
            try:
                await event.delete()
            except TelegramBadRequest:
                pass
        return result


async def set_commands(bot: Bot) -> None:
    private_commands = [
        BotCommand(command="start", description="Start game"),
        BotCommand(command="lang", description="Change language"),
        BotCommand(command="profile", description="Profile"),
        BotCommand(command="commands", description="Commands"),
        BotCommand(command="roles", description="Rules"),
        BotCommand(command="top", description="TOP Rating"),
    ]
    group_commands = [
        BotCommand(command="start", description="Start"),
        BotCommand(command="game", description="Start registration"),
        BotCommand(command="leave", description="Leave game"),
        BotCommand(command="teamgame", description="Start turnire game"),
        BotCommand(command="extend", description="Extend registration timeout"),
        BotCommand(command="lang", description="Change language"),
        BotCommand(command="give", description="Give diamonds"),
        BotCommand(command="gsend", description="Boost premium group"),
        BotCommand(command="shop", description="Shop"),
        BotCommand(command="profile", description="Profile"),
        BotCommand(command="commands", description="Commands"),
        BotCommand(command="roles", description="Rules"),
        BotCommand(command="settings", description="Settings"),
        BotCommand(command="settimeout", description="Set registration timeout"),
        BotCommand(command="stop", description="Stop game"),
        BotCommand(command="top", description="TOP Rating"),
        BotCommand(command="lastwords", description="Set last words"),
        BotCommand(command="gun", description="Use gun"),
    ]
    await bot.set_my_commands(private_commands, scope=BotCommandScopeDefault())
    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    await init_db()

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    if me.username:
        settings.bot_username = me.username
        logging.info("Using bot username from Telegram: @%s", me.username)
    dp = Dispatcher()
    dp.message.middleware(DeleteGroupCommandMiddleware())

    engine = GameEngine(settings=settings, session_factory=SessionLocal)
    await engine.cleanup_stale_games_on_startup()

    dp.include_router(start.router)
    dp.include_router(language.router)
    dp.include_router(game.router)
    dp.include_router(roles.router)
    dp.include_router(profile.router)
    dp.include_router(economy.router)
    dp.include_router(settings_handler.router)
    dp.include_router(top.router)
    dp.include_router(callbacks.router)
    dp.include_router(admin.router)

    start_scheduler()
    scheduler.add_job(
        engine.registration_watchdog,
        "interval",
        seconds=5,
        args=[bot],
        id="registration_watchdog",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=15,
    )
    await set_commands(bot)

    try:
        await dp.start_polling(bot, engine=engine, settings=settings)
    finally:
        shutdown_scheduler()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
