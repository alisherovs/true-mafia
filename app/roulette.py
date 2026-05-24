from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Optional, Union

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import DollarTransaction, GameHistory, RouletteBet, RouletteRound, User

logger = logging.getLogger(__name__)

ROULETTE_MIN_BET = 100
ROULETTE_MAX_BET = 100_000
ROULETTE_ROUND_SECONDS = 30
ROULETTE_SEPARATOR = "━━━━━━━━━━━━━━━━━━"
ROULETTE_MONEY_EMOJI_ID = "5409048419211682843"
ROULETTE_COLORS = {
    "red": ("🔴", "Qizil", 2),
    "black": ("⚫", "Qora", 2),
    "green": ("🟢", "Yashil", 14),
}
ROULETTE_WHEEL = (["red"] * 47) + (["black"] * 47) + (["green"] * 6)
ROULETTE_SPIN_FRAMES = ("🔴", "⚫", "🔴", "⚫", "🟢", "🔴", "⚫")
ROULETTE_SPIN_DELAY_SECONDS = 0.45


@dataclass(frozen=True)
class RouletteView:
    text: str
    keyboard: Optional[InlineKeyboardMarkup] = None
    alert: str = ""
    show_alert: bool = False
    round_id: Optional[int] = None


def _utcnow() -> datetime:
    return datetime.utcnow()


def _ce(symbol: str, emoji_id: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{symbol}</tg-emoji>'


def _money() -> str:
    return _ce("💵", ROULETTE_MONEY_EMOJI_ID)


def _user_link(user: Union[User, RouletteBet]) -> str:
    if isinstance(user, RouletteBet):
        name = user.display_name or "User"
        telegram_id = user.telegram_id
    else:
        name = user.display_name or user.username or "User"
        telegram_id = user.telegram_id
    return f'<a href="tg://user?id={int(telegram_id)}">{escape(name)}</a>'


def parse_roulette_callback(data: str) -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    parts = (data or "").split(":")
    if len(parts) < 2 or parts[0] != "roulette":
        raise ValueError("bad_callback")
    action = parts[1]
    if action == "menu" and len(parts) == 3 and parts[2].isdigit():
        return action, int(parts[2]), None, None
    if action == "custom" and len(parts) == 3 and parts[2].isdigit():
        return action, int(parts[2]), None, None
    if action == "bet" and len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
        return action, int(parts[2]), int(parts[3]), None
    if action == "place" and len(parts) == 5 and parts[2].isdigit() and parts[3].isdigit() and parts[4] in ROULETTE_COLORS:
        return action, int(parts[2]), int(parts[3]), parts[4]
    if action == "noop":
        return action, None, None, None
    raise ValueError("bad_callback")


def roulette_start_text() -> str:
    return (
        f"{ROULETTE_SEPARATOR}\n"
        "🎡 <b>ROULETTE RUSH</b>\n"
        f"{ROULETTE_SEPARATOR}\n\n"
        "Multiplayer ruletka.\n"
        "Istalgancha o'yinchi bitta raundga qo'shiladi.\n\n"
        f"{_money()} <b>Stavkani tanlang</b>"
    )


def roulette_bet_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="100 ⭐", callback_data=f"roulette:bet:{owner_id}:100"),
                InlineKeyboardButton(text="500 ⭐", callback_data=f"roulette:bet:{owner_id}:500"),
                InlineKeyboardButton(text="1000 ⭐", callback_data=f"roulette:bet:{owner_id}:1000"),
            ],
            [
                InlineKeyboardButton(text="5000 ⭐", callback_data=f"roulette:bet:{owner_id}:5000"),
                InlineKeyboardButton(text="10000 ⭐", callback_data=f"roulette:bet:{owner_id}:10000"),
            ],
            [InlineKeyboardButton(text="✍️ Boshqa summa", callback_data=f"roulette:custom:{owner_id}")],
            [InlineKeyboardButton(text="⬅️ Ortga", callback_data=f"qmenu:back:{owner_id}")],
        ]
    )


def roulette_color_text(amount: int) -> str:
    return (
        f"{ROULETTE_SEPARATOR}\n"
        "🎡 <b>ROULETTE RUSH</b>\n"
        f"{ROULETTE_SEPARATOR}\n\n"
        f"{_money()} Stavka: <b>{int(amount)}</b> dollar\n\n"
        "Rangni tanlang:"
    )


def roulette_color_keyboard(owner_id: int, amount: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔴 Qizil x2", callback_data=f"roulette:place:{owner_id}:{amount}:red"),
                InlineKeyboardButton(text="⚫ Qora x2", callback_data=f"roulette:place:{owner_id}:{amount}:black"),
            ],
            [InlineKeyboardButton(text="🟢 Yashil x14", callback_data=f"roulette:place:{owner_id}:{amount}:green")],
            [InlineKeyboardButton(text="⬅️ Stavkaga qaytish", callback_data=f"roulette:menu:{owner_id}")],
        ]
    )


def _round_keyboard(round_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎡 Stavka qo'yish", callback_data="roulette:noop")],
            [InlineKeyboardButton(text="Yangi stavka uchun /qimor", callback_data="roulette:noop")],
        ]
    )


def _choice_label(choice: str) -> str:
    emoji, label, multiplier = ROULETTE_COLORS.get(choice, ("▫️", choice, 1))
    return f"{emoji} {label} x{multiplier}"


class RouletteEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def place_bet(self, tg_user: TelegramUser, chat_id: int, amount: int, choice: str) -> RouletteView:
        amount = int(amount)
        if amount < ROULETTE_MIN_BET or amount > ROULETTE_MAX_BET:
            return RouletteView(
                f"❌ Stavka <b>{ROULETTE_MIN_BET}</b> dan <b>{ROULETTE_MAX_BET}</b> dollargacha bo'lishi kerak.",
                roulette_bet_keyboard(int(tg_user.id)),
                "Stavka noto'g'ri.",
                True,
            )
        if choice not in ROULETTE_COLORS:
            return RouletteView("", None, "Rang noto'g'ri.", True)
        async with self.session_factory() as session:
            async with session.begin():
                user = await self._get_or_create_user(session, tg_user)
                if int(user.dollar or 0) < amount:
                    return RouletteView("❌ Balansingiz yetarli emas.", None, "Balans yetarli emas.", True)
                round_ = await self._active_round(session, int(chat_id))
                if round_ is None:
                    round_ = RouletteRound(
                        chat_id=int(chat_id),
                        status="active",
                        ends_at=_utcnow() + timedelta(seconds=ROULETTE_ROUND_SECONDS),
                    )
                    session.add(round_)
                    await session.flush()
                existing = await session.scalar(
                    select(RouletteBet).where(RouletteBet.round_id == int(round_.id), RouletteBet.user_id == int(user.id))
                )
                if existing is not None:
                    return RouletteView(
                        await self._round_text(session, round_),
                        _round_keyboard(int(round_.id)),
                        "Siz bu raundga stavka qo'ygansiz.",
                        True,
                        int(round_.id),
                    )

                user.dollar = max(0, int(user.dollar or 0) - amount)
                bet = RouletteBet(
                    round_id=int(round_.id),
                    user_id=int(user.id),
                    telegram_id=int(user.telegram_id),
                    display_name=(user.display_name or "User")[:255],
                    choice=choice,
                    amount=amount,
                    status="pending",
                )
                round_.total_bet = int(round_.total_bet or 0) + amount
                session.add(bet)
                self._record_dollar(session, user, -amount, "roulette_bet", f"Ruletka #{round_.id}: {_choice_label(choice)}", chat_id)
                await session.flush()
                logger.info("roulette_bet user=%s round=%s amount=%s choice=%s", tg_user.id, round_.id, amount, choice)
                return RouletteView(
                    await self._round_text(session, round_),
                    _round_keyboard(int(round_.id)),
                    "Stavka qabul qilindi.",
                    False,
                    int(round_.id),
                )

    async def set_message_id(self, round_id: int, message_id: int) -> None:
        async with self.session_factory() as session:
            round_ = await session.get(RouletteRound, int(round_id))
            if round_ is None:
                return
            round_.message_id = int(message_id)
            await session.commit()

    async def resolve_due_rounds(self, bot: Bot, limit: int = 20) -> None:
        now = _utcnow()
        async with self.session_factory() as session:
            rounds = (
                await session.execute(
                    select(RouletteRound)
                    .where(RouletteRound.status == "active", RouletteRound.ends_at <= now)
                    .order_by(RouletteRound.ends_at.asc())
                    .limit(limit)
                    .with_for_update()
                )
            ).scalars().all()
            for round_ in rounds:
                text = await self._resolve_round(session, round_)
                await session.commit()
                await self._publish_result(bot, round_, text)

    async def _resolve_round(self, session: AsyncSession, round_: RouletteRound) -> str:
        bets = (
            await session.execute(select(RouletteBet).where(RouletteBet.round_id == int(round_.id)).order_by(RouletteBet.id.asc()))
        ).scalars().all()
        result = secrets.choice(ROULETTE_WHEEL)
        result_emoji, result_label, _ = ROULETTE_COLORS[result]
        round_.status = "finished"
        round_.result_color = result
        round_.result_label = f"{result_emoji} {result_label}"
        total_payout = 0
        winners: list[str] = []
        for bet in bets:
            user = await session.get(User, int(bet.user_id))
            if user is None:
                bet.status = "lost"
                continue
            multiplier = ROULETTE_COLORS[bet.choice][2]
            if bet.choice == result:
                payout = int(bet.amount) * int(multiplier)
                user.dollar = int(user.dollar or 0) + payout
                bet.payout = payout
                bet.status = "won"
                total_payout += payout
                winners.append(f"{_user_link(bet)} - <b>{payout}</b> dollar")
                self._record_dollar(session, user, payout, "roulette_win", f"Ruletka #{round_.id}: {round_.result_label}", int(round_.chat_id))
                session.add(
                    GameHistory(
                        user_id=int(user.id),
                        game_type="roulette",
                        bet_amount=int(bet.amount),
                        result="won",
                        multiplier=float(multiplier),
                        win_amount=payout,
                    )
                )
            else:
                bet.status = "lost"
                session.add(
                    GameHistory(
                        user_id=int(user.id),
                        game_type="roulette",
                        bet_amount=int(bet.amount),
                        result="lost",
                        multiplier=0.0,
                        win_amount=0,
                    )
                )
        round_.total_payout = total_payout
        return self._result_text(round_, bets, winners)

    async def _publish_result(self, bot: Bot, round_: RouletteRound, text: str) -> None:
        message_id = int(round_.message_id or 0)
        try:
            if message_id:
                await self._animate_round(bot, int(round_.chat_id), message_id, round_)
                await bot.edit_message_text(text, int(round_.chat_id), message_id, reply_markup=None)
            else:
                sent = await bot.send_message(int(round_.chat_id), self._spin_text(round_, "🎡"))
                message_id = int(sent.message_id)
                await self._animate_round(bot, int(round_.chat_id), message_id, round_)
                await bot.edit_message_text(text, int(round_.chat_id), message_id, reply_markup=None)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("roulette_publish_failed round=%s error=%s", round_.id, exc)

    async def _animate_round(self, bot: Bot, chat_id: int, message_id: int, round_: RouletteRound) -> None:
        frames = list(ROULETTE_SPIN_FRAMES)
        if round_.result_label:
            frames.append(str(round_.result_label).split(" ", 1)[0])
        for frame in frames:
            try:
                await bot.edit_message_text(self._spin_text(round_, frame), chat_id, message_id, reply_markup=None)
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    logger.debug("roulette_spin_edit_failed round=%s error=%s", round_.id, exc)
            except TelegramForbiddenError as exc:
                logger.warning("roulette_spin_forbidden round=%s error=%s", round_.id, exc)
                return
            await asyncio.sleep(ROULETTE_SPIN_DELAY_SECONDS)

    def _spin_text(self, round_: RouletteRound, frame: str) -> str:
        return (
            f"{ROULETTE_SEPARATOR}\n"
            "🎡 <b>ROULETTE RUSH</b>\n"
            f"{ROULETTE_SEPARATOR}\n\n"
            "Ruletka aylanmoqda...\n\n"
            f"        {frame}\n\n"
            f"{_money()} Bank: <b>{int(round_.total_bet or 0)}</b> dollar\n"
            f"{ROULETTE_SEPARATOR}"
        )

    async def _round_text(self, session: AsyncSession, round_: RouletteRound) -> str:
        bets = (
            await session.execute(select(RouletteBet).where(RouletteBet.round_id == int(round_.id)).order_by(RouletteBet.id.asc()))
        ).scalars().all()
        left = max(0, int((round_.ends_at - _utcnow()).total_seconds()))
        lines = [
            f"{ROULETTE_SEPARATOR}",
            "🎡 <b>ROULETTE RUSH</b>",
            f"{ROULETTE_SEPARATOR}",
            "",
            f"⏳ Boshlanish: <b>{left}</b> soniya",
            f"{_money()} Bank: <b>{int(round_.total_bet or 0)}</b> dollar",
            f"👥 O'yinchilar: <b>{len(bets)}</b>",
            "",
            f"{ROULETTE_SEPARATOR}",
        ]
        if bets:
            for index, bet in enumerate(bets[-12:], start=max(1, len(bets) - 11)):
                lines.append(f"{index}. {_user_link(bet)} - {_choice_label(bet.choice)} - <b>{int(bet.amount)}</b>")
        else:
            lines.append("Hali stavka yo'q.")
        lines.append(f"{ROULETTE_SEPARATOR}")
        return "\n".join(lines)

    def _result_text(self, round_: RouletteRound, bets: list[RouletteBet], winners: list[str]) -> str:
        lines = [
            "🏆 <b>Ruletka yakunlandi</b>",
            f"{ROULETTE_SEPARATOR}",
            f"🎯 Natija: <b>{round_.result_label}</b>",
            f"{_money()} Bank: <b>{int(round_.total_bet or 0)}</b> dollar",
            f"💰 Yutuq: <b>{int(round_.total_payout or 0)}</b> dollar",
            f"👥 O'yinchilar: <b>{len(bets)}</b>",
            f"{ROULETTE_SEPARATOR}",
        ]
        if winners:
            lines.append("G'oliblar:")
            lines.extend(winners[:12])
        else:
            lines.append("G'olib yo'q. Bank kuyib ketdi.")
        lines.append(f"{ROULETTE_SEPARATOR}")
        return "\n".join(lines)

    async def _active_round(self, session: AsyncSession, chat_id: int) -> Optional[RouletteRound]:
        return await session.scalar(
            select(RouletteRound)
            .where(RouletteRound.chat_id == int(chat_id), RouletteRound.status == "active")
            .order_by(RouletteRound.id.desc())
            .with_for_update()
        )

    async def _get_or_create_user(self, session: AsyncSession, tg_user: TelegramUser) -> User:
        user = await session.scalar(select(User).where(User.telegram_id == int(tg_user.id)).with_for_update())
        display_name = (getattr(tg_user, "full_name", None) or getattr(tg_user, "first_name", None) or "User")[:255]
        username = getattr(tg_user, "username", None)
        if user is None:
            user = User(telegram_id=int(tg_user.id), username=username, display_name=display_name, dollar=0)
            session.add(user)
            await session.flush()
        else:
            user.username = username
            user.display_name = display_name
        return user

    def _record_dollar(self, session: AsyncSession, user: User, amount: int, action: str, note: str, chat_id: int) -> None:
        if amount == 0:
            return
        session.add(
            DollarTransaction(
                user_telegram_id=int(user.telegram_id),
                user_name=(user.display_name or "User")[:255],
                amount=int(amount),
                balance_after=int(user.dollar or 0),
                action=action[:64],
                note=note,
                chat_id=chat_id,
            )
        )
