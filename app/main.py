from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, BotCommandScopeDefault
from aiogram.types import CallbackQuery
from aiogram.types import ErrorEvent, Message
from sqlalchemy import select

from app.config import get_settings
from app.credit import CreditService
from app.database import SessionLocal, init_db
from app.game_engine import GameEngine
from app.handlers import admin, callbacks, economy, emoji_debug, gamble, game, hero, language, profile, roles, settings as settings_handler, start, top
from app.models import CreditBlockedUser
from app.scheduler import scheduler, shutdown_scheduler, start_scheduler


class CreditBlockMessageMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if event.from_user:
            async with SessionLocal() as session:
                blocked = (
                    await session.execute(
                        select(CreditBlockedUser.telegram_id).where(CreditBlockedUser.telegram_id == event.from_user.id)
                    )
                ).scalar_one_or_none()
            if blocked is not None:
                try:
                    await event.answer("🚫 Kredit qarzi muddatida so'ndirilmagani uchun botdan bloklangansiz.")
                except (TelegramBadRequest, TelegramForbiddenError):
                    pass
                return None
        return await handler(event, data)


class CreditBlockCallbackMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        if event.from_user:
            async with SessionLocal() as session:
                blocked = (
                    await session.execute(
                        select(CreditBlockedUser.telegram_id).where(CreditBlockedUser.telegram_id == event.from_user.id)
                    )
                ).scalar_one_or_none()
            if blocked is not None:
                await event.answer("🚫 Kredit qarzi sabab botdan bloklangansiz.", show_alert=True)
                return None
        return await handler(event, data)


class DeleteGroupCommandMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        result = await handler(event, data)
        if event.chat.type != "private" and event.text and event.text.startswith("/"):
            if event.from_user:
                engine: GameEngine = data["engine"]
                if await engine.is_vip_user_active(event.from_user.id):
                    return result
            try:
                await event.delete()
            except (TelegramBadRequest, TelegramForbiddenError):
                pass
        return result


class ChatRestrictionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if event.chat.type != "private" and event.from_user:
            engine: GameEngine = data["engine"]
            allowed = await engine.check_chat_write_permission(
                event.bot, event.chat.id, event.from_user.id
            )
            if not allowed:
                try:
                    await event.delete()
                except (TelegramBadRequest, TelegramForbiddenError):
                    pass
                return None
        return await handler(event, data)


async def global_error_handler(event: ErrorEvent) -> bool:
    exc = event.exception
    if isinstance(exc, TelegramForbiddenError):
        logging.warning("Telegram forbidden (bot kicked/blocked): %s", exc)
        return True
    if isinstance(exc, TelegramBadRequest):
        msg = str(exc).lower()
        ignorable = (
            "message to be replied not found",
            "message to delete not found",
            "message to edit not found",
            "message can't be deleted",
            "message is not modified",
            "chat not found",
            "have no rights to send a message",
            "not enough rights",
            "user is deactivated",
            "bot was blocked by the user",
            "query is too old",
        )
        if any(s in msg for s in ignorable):
            logging.warning("Ignoring Telegram error: %s", exc)
            return True
    if isinstance(exc, TelegramRetryAfter):
        logging.warning("Telegram rate-limit: retry after %s", getattr(exc, "retry_after", "?"))
        return True
    return False


async def set_commands(bot: Bot) -> None:
    private_commands = [
        BotCommand(command="start", description="O'yinni boshlash"),
        BotCommand(command="profile", description="Profilingizni ko'rish (Shaxsiy chatda)"),
        BotCommand(command="roles", description="O'yin rollarini ko'rish"),
    ]
    group_commands = [
        BotCommand(command="start", description="O'yinni boshlash"),
        BotCommand(command="game", description="Ro'yxatdan o'tishni boshlash"),
        BotCommand(command="turnir", description="Turnir o'yinini boshlash"),
        BotCommand(command="classic", description="Classic mode"),
        BotCommand(command="super", description="Super mode"),
        BotCommand(command="mega", description="Mega mode"),
        BotCommand(command="leave", description="O'yindan chiqish"),
        BotCommand(command="extend", description="Ro'yxat vaqtini uzaytirish"),
        BotCommand(command="stop", description="O'yinni to'xtatish"),
        BotCommand(command="teamgame", description="Turnir o'yini"),
        BotCommand(command="lastwords", description="O'lim oldi so'zi"),
        BotCommand(command="settings", description="Guruh sozlamalari"),
        BotCommand(command="settimeout", description="Ro'yxat vaqtini sozlash"),
        BotCommand(command="setnight", description="Tun vaqtini sozlash"),
        BotCommand(command="setdiscussion", description="Kun muhokamasini sozlash"),
        BotCommand(command="setvoting", description="Ovoz berish vaqtini sozlash"),
        BotCommand(command="setminplayers", description="Minimal o'yinchilarni sozlash"),
        BotCommand(command="lang", description="Tilni o'zgartirish"),
        BotCommand(command="profile", description="Profilingiz"),
        BotCommand(command="shop", description="Do'kon"),
        BotCommand(command="qimor", description="Mines qimor"),
        BotCommand(command="topq", description="Haftalik qimor TOP"),
        BotCommand(command="give", description="Almaz berish"),
        BotCommand(command="gsend", description="Premium guruh reytingi"),
        BotCommand(command="roles", description="Rollar"),
        BotCommand(command="top", description="TOP reyting"),
        BotCommand(command="commands", description="Buyruqlar"),
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
    dp.message.middleware(CreditBlockMessageMiddleware())
    dp.callback_query.middleware(CreditBlockCallbackMiddleware())
    dp.message.middleware(DeleteGroupCommandMiddleware())
    dp.message.middleware(ChatRestrictionMiddleware())
    dp.errors.register(global_error_handler)

    engine = GameEngine(settings=settings, session_factory=SessionLocal)
    await engine.cleanup_stale_games_on_startup()

    dp.include_router(start.router)
    dp.include_router(language.router)
    dp.include_router(game.router)
    dp.include_router(roles.router)
    dp.include_router(profile.router)
    dp.include_router(economy.router)
    dp.include_router(gamble.router)
    dp.include_router(hero.router)
    dp.include_router(settings_handler.router)
    dp.include_router(top.router)
    dp.include_router(callbacks.router)
    dp.include_router(admin.router)
    dp.include_router(emoji_debug.router)

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
    scheduler.add_job(
        engine.premium_reset_watchdog,
        "interval",
        seconds=60,
        id="premium_reset_watchdog",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=30,
    )
    scheduler.add_job(
        engine.send_pending_diamond_logs,
        "interval",
        seconds=30,
        args=[bot],
        id="diamond_log_watchdog",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=30,
    )
    credit_service = CreditService(SessionLocal)
    scheduler.add_job(
        credit_service.daily_watchdog,
        "cron",
        hour=5,
        minute=0,
        args=[bot],
        id="credit_daily_watchdog",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    await set_commands(bot)

    try:
        await dp.start_polling(bot, engine=engine, settings=settings)
    finally:
        shutdown_scheduler()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
