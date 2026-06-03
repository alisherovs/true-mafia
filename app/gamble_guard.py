from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import DollarTransaction, GambleMinesGame, GameHistory, User

logger = logging.getLogger(__name__)

UZ_TZ = timezone(timedelta(hours=5))
GAMBLE_OVERWIN_RESET_THRESHOLD = 31_000
GAMBLE_OVERWIN_RECOVERY_BALANCE = 2_000
GAMBLE_OVERWIN_RESET_ACTION = "gamble_overwin_reset"


def _daily_window_start_utc() -> datetime:
    now_local = datetime.now(UZ_TZ)
    start_local = datetime.combine(now_local.date(), time.min, tzinfo=UZ_TZ)
    return start_local.astimezone(timezone.utc)


async def enforce_gamble_overwin_guard(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Optional[Bot],
    telegram_id: int,
) -> bool:
    triggered = False
    async with session_factory() as session:
        async with session.begin():
            user = await session.scalar(select(User).where(User.telegram_id == int(telegram_id)).with_for_update())
            if user is None:
                return False
            if _is_vip_active(user):
                return False
            since = await _guard_window_start(session, int(user.telegram_id))
            total = await _winnings_since(session, user, since)
            if total <= GAMBLE_OVERWIN_RESET_THRESHOLD:
                return False

            old_balance = int(user.dollar or 0)
            user.dollar = GAMBLE_OVERWIN_RECOVERY_BALANCE
            session.add(
                DollarTransaction(
                    user_telegram_id=int(user.telegram_id),
                    user_name=(user.display_name or "User")[:255],
                    amount=GAMBLE_OVERWIN_RECOVERY_BALANCE - old_balance,
                    balance_after=GAMBLE_OVERWIN_RECOVERY_BALANCE,
                    action=GAMBLE_OVERWIN_RESET_ACTION,
                    note="Qimor anti-abuse: o'yindagi xatolik yoki boshqa sabab bilan pul ko'paytirish ehtimoli",
                    chat_id=None,
                )
            )
            triggered = True
            logger.warning("gamble_overwin_reset user=%s total=%s old_balance=%s", telegram_id, total, old_balance)

    if triggered and bot is not None:
        await _notify_user(bot, int(telegram_id))
    return triggered


def _is_vip_active(user: User) -> bool:
    vip_until = user.vip_until
    if vip_until is None:
        return False
    if vip_until.tzinfo is None:
        vip_until = vip_until.replace(tzinfo=timezone.utc)
    else:
        vip_until = vip_until.astimezone(timezone.utc)
    return vip_until > datetime.now(timezone.utc)


async def _guard_window_start(session: AsyncSession, telegram_id: int) -> datetime:
    daily_start = _daily_window_start_utc()
    last_reset = await session.scalar(
        select(func.max(DollarTransaction.created_at)).where(
            DollarTransaction.user_telegram_id == int(telegram_id),
            DollarTransaction.action == GAMBLE_OVERWIN_RESET_ACTION,
            DollarTransaction.created_at >= daily_start,
        )
    )
    if last_reset is None:
        return daily_start
    if getattr(last_reset, "tzinfo", None) is None:
        last_reset = last_reset.replace(tzinfo=timezone.utc)
    return max(daily_start, last_reset)


async def _winnings_since(session: AsyncSession, user: User, since: datetime) -> int:
    history_total = int(
        await session.scalar(
            select(func.coalesce(func.sum(GameHistory.win_amount), 0)).where(
                GameHistory.user_id == int(user.id),
                GameHistory.win_amount > 0,
                GameHistory.created_at >= since,
            )
        )
        or 0
    )
    mines_total = int(
        await session.scalar(
            select(func.coalesce(func.sum(GambleMinesGame.payout), 0)).where(
                GambleMinesGame.status == "cashed",
                GambleMinesGame.payout > 0,
                GambleMinesGame.ended_at.is_not(None),
                GambleMinesGame.ended_at >= since,
                or_(
                    GambleMinesGame.winner_telegram_id == int(user.telegram_id),
                    (
                        GambleMinesGame.winner_telegram_id.is_(None)
                        & (GambleMinesGame.user_telegram_id == int(user.telegram_id))
                    ),
                ),
            )
        )
        or 0
    )
    return history_total + mines_total


async def _notify_user(bot: Bot, telegram_id: int) -> None:
    text = (
        "⚠️ <b>Qimor anti-abuse tekshiruvi</b>\n\n"
        "Hisobingizdagi dollarlar 0 ga tenglashtirildi va sizga <b>2000 dollar</b> qoldirildi.\n\n"
        "Sabab: o'yindagi xatolik yoki boshqa sabab bilan pul ko'paytirish ehtimoli aniqlandi.\n\n"
        "Agar bu holat yana takrorlansa, botdan ban qilinishingiz mumkin."
    )
    try:
        await bot.send_message(int(telegram_id), text)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass
