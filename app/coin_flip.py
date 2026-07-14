"""
Premium CoinFlip mini-game for True Mafia.

Integrates with User.dollar, DollarTransaction, GameHistory.
Server-side only RNG, per-user locks, full audit trail.
UI language: Uzbek.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CoinFlipGame, CoinFlipUserStats, DollarTransaction, GameHistory, User

logger = logging.getLogger(__name__)

COIN_GAME_TYPE = "coinflip"
COIN_MIN_BET = 100
COIN_MAX_BET = 100_000
COIN_MULTIPLIER = 2.0
COIN_HEADS = "heads"
COIN_TAILS = "tails"
COIN_SIDES = (COIN_HEADS, COIN_TAILS)
COIN_SEP = "━━━━━━━━━━━━━━━"
COIN_MONEY_EMOJI_ID = "5409048419211682843"

_USER_LOCKS: dict[int, asyncio.Lock] = {}
_IN_FLIGHT: set[int] = set()


@dataclass(frozen=True)
class CoinView:
    text: str
    keyboard: Optional[InlineKeyboardMarkup] = None
    alert: str = ""
    show_alert: bool = False
    played: bool = False
    won: bool = False
    bet: int = 0
    choice: str = ""
    result: str = ""
    profit: int = 0
    balance: int = 0
    game_id: Optional[int] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ce(symbol: str, emoji_id: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{symbol}</tg-emoji>'


def _money() -> str:
    return _ce("💵", COIN_MONEY_EMOJI_ID)


def _btn(text: str, callback_data: str, style: str = "primary") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data, **{"style": style})


def _user_lock(telegram_id: int) -> asyncio.Lock:
    lock = _USER_LOCKS.get(int(telegram_id))
    if lock is None:
        lock = asyncio.Lock()
        _USER_LOCKS[int(telegram_id)] = lock
    return lock


def side_label(side: str) -> str:
    return "🦅 Gerb" if side == COIN_HEADS else "🔢 Raqam"


def side_emoji(side: str) -> str:
    return "🦅" if side == COIN_HEADS else "🔢"


def parse_coin_callback(data: str) -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    """
    Returns (action, owner_id, amount, extra).
    extra = side for side/go, mul string for x.
    """
    parts = (data or "").split(":")
    if len(parts) < 2 or parts[0] != "cf":
        raise ValueError("bad_callback")
    action = parts[1]

    if action in {"menu", "half", "all", "min", "max", "custom", "again", "stats", "back"} and len(parts) >= 3:
        if not parts[2].isdigit():
            raise ValueError("bad_callback")
        return action, int(parts[2]), None, None

    if action == "bet" and len(parts) >= 4 and parts[2].isdigit() and parts[3].isdigit():
        return action, int(parts[2]), int(parts[3]), None

    if action == "x" and len(parts) >= 5 and parts[2].isdigit() and parts[3].isdigit() and parts[4].isdigit():
        return action, int(parts[2]), int(parts[3]), parts[4]

    if action in {"side", "go"} and len(parts) >= 5 and parts[2].isdigit() and parts[3].isdigit() and parts[4] in COIN_SIDES:
        return action, int(parts[2]), int(parts[3]), parts[4]

    raise ValueError("bad_callback")


def validate_bet(amount: int, balance: int) -> tuple[bool, str]:
    if amount <= 0:
        return False, "Stavka noto‘g‘ri."
    if amount < COIN_MIN_BET:
        return False, f"Min stavka: <b>{COIN_MIN_BET}</b> {_money()}"
    if amount > COIN_MAX_BET:
        return False, f"Max stavka: <b>{COIN_MAX_BET}</b> {_money()}"
    if amount > balance:
        return False, f"Balans yetarli emas.\nBalans: <b>{balance}</b> {_money()}"
    return True, ""


def coin_start_text(balance: int) -> str:
    return (
        f"{COIN_SEP}\n"
        f"🪙 <b>COIN FLIP</b>\n"
        f"{COIN_SEP}\n\n"
        f"💎 Premium tanga o‘yini\n"
        f"🎯 Yutuq: <b>x{COIN_MULTIPLIER:.0f}</b> (to‘g‘ri tanlov)\n\n"
        f"{_money()} Balans: <b>{int(balance)}</b>\n"
        f"📉 Min: <b>{COIN_MIN_BET}</b>  ·  📈 Max: <b>{COIN_MAX_BET}</b>\n\n"
        f"💰 <b>Stavkani tanlang</b>"
    )


def build_coin_bet_keyboard(owner_id: int, balance: int = 0) -> InlineKeyboardMarkup:
    o = int(owner_id)
    max_shown = min(COIN_MAX_BET, max(COIN_MIN_BET, int(balance)))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("100", f"cf:bet:{o}:100", "primary"),
                _btn("500", f"cf:bet:{o}:500", "primary"),
                _btn("1000", f"cf:bet:{o}:1000", "primary"),
            ],
            [
                _btn("5000", f"cf:bet:{o}:5000", "primary"),
                _btn("10000", f"cf:bet:{o}:10000", "primary"),
            ],
            [
                _btn(f"Min {COIN_MIN_BET}", f"cf:min:{o}", "success"),
                _btn(f"Max {max_shown}", f"cf:max:{o}", "success"),
            ],
            [
                _btn("½ Balans", f"cf:half:{o}", "primary"),
                _btn("🔥 All In", f"cf:all:{o}", "danger"),
            ],
            [_btn("✍️ Boshqa summa", f"cf:custom:{o}", "success")],
            [
                _btn("📊 Statistika", f"cf:stats:{o}", "primary"),
                _btn("⬅️ Ortga", f"cf:back:{o}", "danger"),
            ],
        ]
    )


def build_side_keyboard(owner_id: int, bet: int) -> InlineKeyboardMarkup:
    o, b = int(owner_id), int(bet)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("🦅 Gerb", f"cf:side:{o}:{b}:{COIN_HEADS}", "success"),
                _btn("🔢 Raqam", f"cf:side:{o}:{b}:{COIN_TAILS}", "primary"),
            ],
            [
                _btn("x2", f"cf:x:{o}:{b}:2", "primary"),
                _btn("x5", f"cf:x:{o}:{b}:5", "primary"),
                _btn("x10", f"cf:x:{o}:{b}:10", "primary"),
            ],
            [_btn("⬅️ Stavka", f"cf:menu:{o}", "danger")],
        ]
    )


def side_select_text(balance: int, bet: int) -> str:
    return (
        f"{COIN_SEP}\n"
        f"🪙 <b>COIN FLIP</b>\n"
        f"{COIN_SEP}\n\n"
        f"{_money()} Stavka: <b>{int(bet)}</b>\n"
        f"💼 Balans: <b>{int(balance)}</b>\n"
        f"🏆 Yutuq: <b>{int(bet) * int(COIN_MULTIPLIER)}</b>\n\n"
        f"Tomoni tanlang:"
    )


def confirm_text(balance: int, bet: int, choice: str) -> str:
    return (
        f"{COIN_SEP}\n"
        f"🪙 <b>TASDIQLASH</b>\n"
        f"{COIN_SEP}\n\n"
        f"{_money()} Stavka: <b>{int(bet)}</b>\n"
        f"🎯 Tanlov: <b>{side_label(choice)}</b>\n"
        f"💼 Balans: <b>{int(balance)}</b>\n"
        f"✨ Sof foyda: <b>+{int(bet)}</b>\n\n"
        f"Tanga tashlansinmi?"
    )


def build_confirm_keyboard(owner_id: int, bet: int, choice: str) -> InlineKeyboardMarkup:
    o, b = int(owner_id), int(bet)
    other = COIN_TAILS if choice == COIN_HEADS else COIN_HEADS
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ Tasdiqlash", f"cf:go:{o}:{b}:{choice}", "success")],
            [_btn(f"🔄 {side_label(other)}", f"cf:side:{o}:{b}:{other}", "primary")],
            [_btn("❌ Bekor", f"cf:menu:{o}", "danger")],
        ]
    )


def flipping_text(frame: int = 0) -> str:
    coins = ("🪙", "✨", "💫", "🪙", "✨")
    dots = "·" * ((frame % 3) + 1)
    return (
        f"{COIN_SEP}\n"
        f"🪙 <b>TANGA AYLANMOQDA</b>\n"
        f"{COIN_SEP}\n\n"
        f"      {coins[frame % len(coins)]}  {coins[(frame + 1) % len(coins)]}  {coins[(frame + 2) % len(coins)]}\n\n"
        f"⏳ Kutib turing{dots}"
    )


def result_text(
    *,
    bet: int,
    choice: str,
    result: str,
    won: bool,
    profit: int,
    balance: int,
) -> str:
    title = "🏆 YUTDINGIZ!" if won else "💥 YUTQAZDINGIZ"
    profit_line = (
        f"📈 Sof foyda: <b>+{int(profit)}</b> {_money()}"
        if won
        else f"📉 Zarar: <b>{int(profit)}</b> {_money()}"
    )
    return (
        f"{COIN_SEP}\n"
        f"🪙 <b>COIN FLIP</b>\n"
        f"{COIN_SEP}\n\n"
        f"<b>{title}</b>\n\n"
        f"{_money()} Stavka: <b>{int(bet)}</b>\n"
        f"🎯 Tanlov: <b>{side_label(choice)}</b>\n"
        f"🎲 Natija: <b>{side_emoji(result)} {side_label(result)}</b>\n"
        f"{profit_line}\n"
        f"💼 Yangi balans: <b>{int(balance)}</b>\n\n"
        f"{COIN_SEP}"
    )


def build_result_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    o = int(owner_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🔁 Yana o‘ynash", f"cf:again:{o}", "success")],
            [
                _btn("📊 Statistika", f"cf:stats:{o}", "primary"),
                _btn("⬅️ Ortga", f"cf:back:{o}", "danger"),
            ],
        ]
    )


def stats_text(stats: CoinFlipUserStats | None, balance: int) -> str:
    played = int(stats.games_played) if stats else 0
    wins = int(stats.wins) if stats else 0
    losses = int(stats.losses) if stats else 0
    rate = (wins * 100 / played) if played else 0.0
    profit = int(stats.total_profit) if stats else 0
    biggest = int(stats.biggest_win) if stats else 0
    streak = int(stats.current_streak) if stats else 0
    best = int(stats.best_streak) if stats else 0
    profit_s = f"+{profit}" if profit >= 0 else str(profit)
    return (
        f"{COIN_SEP}\n"
        f"📊 <b>COIN FLIP STATISTIKA</b>\n"
        f"{COIN_SEP}\n\n"
        f"🎮 O‘yinlar: <b>{played}</b>\n"
        f"✅ Yutuq: <b>{wins}</b>\n"
        f"❌ Mag‘lubiyat: <b>{losses}</b>\n"
        f"📈 Win rate: <b>{rate:.1f}%</b>\n"
        f"💰 Umumiy foyda: <b>{profit_s}</b>\n"
        f"🏅 Eng katta yutuq: <b>{biggest}</b>\n"
        f"🔥 Seriya: <b>{streak}</b>\n"
        f"🏆 Eng yaxshi seriya: <b>{best}</b>\n\n"
        f"{_money()} Balans: <b>{int(balance)}</b>\n"
        f"{COIN_SEP}"
    )


def build_stats_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    o = int(owner_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🪙 O‘ynash", f"cf:menu:{o}", "success")],
            [_btn("⬅️ Ortga", f"cf:back:{o}", "danger")],
        ]
    )


def _record_dollar(
    session: AsyncSession,
    user: User,
    amount: int,
    action: str,
    note: str,
    chat_id: int,
) -> None:
    if amount == 0:
        return
    session.add(
        DollarTransaction(
            user_telegram_id=int(user.telegram_id),
            user_name=(user.display_name or "User")[:255],
            amount=int(amount),
            balance_after=int(user.dollar or 0),
            action=action[:64],
            note=(note[:500] if note else None),
            chat_id=int(chat_id),
        )
    )


class CoinFlipEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def get_balance(self, telegram_id: int) -> int:
        async with self.session_factory() as session:
            bal = await session.scalar(select(User.dollar).where(User.telegram_id == int(telegram_id)))
            return int(bal or 0)

    async def home(self, telegram_id: int, owner_id: int | None = None) -> CoinView:
        bal = await self.get_balance(telegram_id)
        oid = int(owner_id or telegram_id)
        return CoinView(coin_start_text(bal), build_coin_bet_keyboard(oid, bal))

    async def resolve_preset_bet(
        self,
        telegram_id: int,
        owner_id: int,
        kind: str,
        base_amount: int | None = None,
        mul: int | None = None,
    ) -> CoinView:
        bal = await self.get_balance(telegram_id)
        amount = 0
        if kind == "bet" and base_amount is not None:
            amount = int(base_amount)
        elif kind == "min":
            amount = COIN_MIN_BET
        elif kind == "max":
            amount = min(COIN_MAX_BET, max(0, bal))
        elif kind == "half":
            amount = max(0, bal // 2)
        elif kind == "all":
            amount = max(0, bal)
        elif kind == "x" and base_amount is not None and mul is not None:
            amount = int(base_amount) * int(mul)
        else:
            return CoinView(
                coin_start_text(bal),
                build_coin_bet_keyboard(owner_id, bal),
                "Noto‘g‘ri stavka.",
                True,
            )

        ok, err = validate_bet(amount, bal)
        if not ok:
            return CoinView(
                f"❌ {err}\n\n{coin_start_text(bal)}",
                build_coin_bet_keyboard(owner_id, bal),
                "Stavka rad etildi.",
                True,
            )
        return CoinView(side_select_text(bal, amount), build_side_keyboard(owner_id, amount))

    async def show_confirm(self, telegram_id: int, owner_id: int, bet: int, choice: str) -> CoinView:
        if choice not in COIN_SIDES:
            return await self.home(telegram_id, owner_id)
        bal = await self.get_balance(telegram_id)
        ok, err = validate_bet(int(bet), bal)
        if not ok:
            return CoinView(
                f"❌ {err}\n\n{coin_start_text(bal)}",
                build_coin_bet_keyboard(owner_id, bal),
                "Stavka rad etildi.",
                True,
            )
        return CoinView(
            confirm_text(bal, int(bet), choice),
            build_confirm_keyboard(owner_id, int(bet), choice),
        )

    async def show_stats(self, telegram_id: int, owner_id: int) -> CoinView:
        async with self.session_factory() as session:
            user = await session.scalar(select(User).where(User.telegram_id == int(telegram_id)))
            stats = await session.get(CoinFlipUserStats, int(telegram_id))
            bal = int(user.dollar or 0) if user else 0
        return CoinView(stats_text(stats, bal), build_stats_keyboard(owner_id))

    async def play(
        self,
        tg_user: TelegramUser,
        chat_id: int,
        bet_amount: int,
        choice: str,
    ) -> CoinView:
        telegram_id = int(tg_user.id)
        if choice not in COIN_SIDES:
            return CoinView("❌ Noto‘g‘ri tanlov.", None, "Noto‘g‘ri tanlov.", True)
        if telegram_id in _IN_FLIGHT:
            return CoinView("", None, "⏳ O‘yin hali tugamadi. Kuting…", True)

        lock = _user_lock(telegram_id)
        if lock.locked():
            return CoinView("", None, "⏳ Biroz kuting…", True)

        async with lock:
            if telegram_id in _IN_FLIGHT:
                return CoinView("", None, "⏳ O‘yin hali tugamadi. Kuting…", True)
            _IN_FLIGHT.add(telegram_id)
            try:
                return await self._play_locked(tg_user, chat_id, int(bet_amount), choice)
            finally:
                _IN_FLIGHT.discard(telegram_id)

    async def _play_locked(
        self,
        tg_user: TelegramUser,
        chat_id: int,
        bet_amount: int,
        choice: str,
    ) -> CoinView:
        async with self.session_factory() as session:
            async with session.begin():
                user = await self._get_or_create_user(session, tg_user)
                bal = int(user.dollar or 0)
                ok, err = validate_bet(bet_amount, bal)
                if not ok:
                    return CoinView(
                        f"❌ {err}",
                        build_coin_bet_keyboard(int(tg_user.id), bal),
                        "Stavka rad etildi.",
                        True,
                    )

                result = secrets.choice(list(COIN_SIDES))
                won = result == choice
                payout = int(bet_amount * COIN_MULTIPLIER) if won else 0
                profit = payout - bet_amount

                user.dollar = bal - bet_amount
                if won:
                    user.dollar = int(user.dollar) + payout
                balance_after = int(user.dollar)

                game = CoinFlipGame(
                    user_id=int(user.id),
                    user_telegram_id=int(user.telegram_id),
                    chat_id=int(chat_id),
                    bet_amount=bet_amount,
                    choice=choice,
                    result=result,
                    won=won,
                    payout=payout,
                    profit=profit,
                    balance_after=balance_after,
                    status="completed",
                    token=secrets.token_hex(8),
                )
                session.add(game)
                await session.flush()

                _record_dollar(
                    session,
                    user,
                    -bet_amount,
                    "coinflip_bet",
                    f"CoinFlip stavka #{game.id} {choice}",
                    chat_id,
                )
                if won and payout > 0:
                    _record_dollar(
                        session,
                        user,
                        payout,
                        "coinflip_win",
                        f"CoinFlip yutuq #{game.id} {result}",
                        chat_id,
                    )

                session.add(
                    GameHistory(
                        user_id=int(user.id),
                        game_type=COIN_GAME_TYPE,
                        bet_amount=bet_amount,
                        result="won" if won else "lost",
                        multiplier=COIN_MULTIPLIER if won else 0.0,
                        win_amount=payout if won else 0,
                    )
                )
                await self._update_stats(session, int(user.telegram_id), bet_amount, won, profit, payout)

                logger.info(
                    "coinflip user=%s game=%s bet=%s choice=%s result=%s won=%s profit=%s bal=%s",
                    tg_user.id,
                    game.id,
                    bet_amount,
                    choice,
                    result,
                    won,
                    profit,
                    balance_after,
                )

                return CoinView(
                    result_text(
                        bet=bet_amount,
                        choice=choice,
                        result=result,
                        won=won,
                        profit=profit,
                        balance=balance_after,
                    ),
                    build_result_keyboard(int(tg_user.id)),
                    "🏆 Yutdingiz!" if won else "💥 Yutqazdingiz",
                    True,
                    played=True,
                    won=won,
                    bet=bet_amount,
                    choice=choice,
                    result=result,
                    profit=profit,
                    balance=balance_after,
                    game_id=int(game.id),
                )

    async def _update_stats(
        self,
        session: AsyncSession,
        telegram_id: int,
        bet: int,
        won: bool,
        profit: int,
        payout: int,
    ) -> None:
        stats = await session.get(CoinFlipUserStats, int(telegram_id))
        if stats is None:
            stats = CoinFlipUserStats(user_telegram_id=int(telegram_id))
            session.add(stats)
            await session.flush()

        stats.games_played = int(stats.games_played or 0) + 1
        stats.total_wagered = int(stats.total_wagered or 0) + int(bet)
        stats.total_profit = int(stats.total_profit or 0) + int(profit)
        if won:
            stats.wins = int(stats.wins or 0) + 1
            stats.current_streak = max(0, int(stats.current_streak or 0)) + 1
            stats.best_streak = max(int(stats.best_streak or 0), int(stats.current_streak))
            stats.biggest_win = max(int(stats.biggest_win or 0), int(payout))
        else:
            stats.losses = int(stats.losses or 0) + 1
            stats.current_streak = 0
        stats.updated_at = _utcnow()

    async def _get_or_create_user(self, session: AsyncSession, tg_user: TelegramUser) -> User:
        user = await session.scalar(
            select(User).where(User.telegram_id == int(tg_user.id)).with_for_update()
        )
        display_name = (
            getattr(tg_user, "full_name", None) or getattr(tg_user, "first_name", None) or "User"
        )[:255]
        username = getattr(tg_user, "username", None)
        if user is None:
            user = User(
                telegram_id=int(tg_user.id),
                username=username,
                display_name=display_name,
                dollar=0,
            )
            session.add(user)
            await session.flush()
        else:
            user.username = username
            user.display_name = display_name
        return user

