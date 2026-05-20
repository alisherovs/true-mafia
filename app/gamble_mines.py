from __future__ import annotations

import json
import asyncio
import random
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from math import floor
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.models import DollarTransaction, GambleMinesGame, GambleUserStats, User


GRID_SIZE = 36
GRID_WIDTH = 6
MIN_BET = 10
DAILY_WIN_LIMIT = 50_000
COOLDOWN_SECONDS = 20
MAX_PAYOUT = DAILY_WIN_LIMIT
HOUSE_PAYOUT_FACTOR = 0.92
_GAME_LOCKS: dict[int, asyncio.Lock] = {}
RICH_TAX_TIERS = (
    (1_000_000, 0.20),
    (250_000, 0.12),
    (100_000, 0.07),
)
MONEY_EMOJI_ID = "5375296873982604963"
DIAMOND_EMOJI_ID = "5471952986970267163"
MINE_EMOJI_ID = "5469654973308476699"
GIFT_EMOJI_ID = "5199749070830197566"
BANK_EMOJI_ID = "5264895611517300926"


def _ce(symbol: str, emoji_id: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{symbol}</tg-emoji>'

VISIBLE_MULTIPLIERS = {
    0: 1.00,
    1: 1.08,
    2: 1.18,
    3: 1.35,
    4: 1.55,
    5: 1.80,
    6: 2.25,
    7: 2.80,
    8: 3.50,
    9: 4.30,
    10: 5.00,
    11: 6.70,
    12: 8.80,
    13: 11.00,
    14: 13.00,
    15: 15.00,
}


@dataclass(frozen=True)
class MinesView:
    text: str
    keyboard: Optional[InlineKeyboardMarkup]
    alert: str = ""
    show_alert: bool = False
    game_id: Optional[int] = None
    token: str = ""


class MinesAntiCheatValidator:
    @staticmethod
    def validate_bet(raw: str | None) -> tuple[bool, int, str]:
        if not raw:
            return False, 0, "Foydalanish: <code>/qimor 100</code>"
        try:
            amount = int(str(raw).strip().split()[0])
        except (TypeError, ValueError):
            return False, 0, "Summa faqat butun son bo'lishi kerak. Masalan: <code>/qimor 100</code>"
        if amount < MIN_BET:
            return False, 0, f"Minimal stavka: <b>{MIN_BET}</b> dollar."
        return True, amount, ""

    @staticmethod
    def decode_callback(data: str) -> tuple[str, int, str, Optional[int]]:
        parts = (data or "").split(":")
        if len(parts) < 4 or parts[0] != "gm":
            raise ValueError("bad_callback")
        action = parts[1]
        game_id = int(parts[2])
        token = parts[3]
        cell = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None
        return action, game_id, token, cell


class MinesEconomyService:
    @staticmethod
    def record_dollar(
        session: AsyncSession,
        user: User,
        amount: int,
        action: str,
        *,
        note: str = "",
        chat_id: Optional[int] = None,
    ) -> None:
        if amount == 0:
            return
        session.add(
            DollarTransaction(
                user_telegram_id=user.telegram_id,
                user_name=(user.display_name or "User")[:255],
                amount=int(amount),
                balance_after=int(user.dollar or 0),
                action=action[:64],
                note=note or None,
                chat_id=chat_id,
            )
        )


class MinesMath:
    @staticmethod
    def visible_multiplier(opened_count: int) -> float:
        if opened_count <= 15:
            return VISIBLE_MULTIPLIERS.get(opened_count, 15.0)
        extra = min(opened_count - 15, 6)
        return min(15.0, 15.0 + extra * 0.0)

    @staticmethod
    def rich_tax(balance_after_bet: int) -> float:
        for threshold, tax in RICH_TAX_TIERS:
            if balance_after_bet >= threshold:
                return tax
        return 0.0

    @staticmethod
    def streak_bonus_rate(streak: int, opened_count: int, bet: int) -> float:
        if streak <= 0 or opened_count < 3 or bet < 50:
            return 0.0
        return min(0.03, streak * 0.005)

    @classmethod
    def payout(cls, bet: int, opened_count: int, balance_after_bet: int, streak: int) -> int:
        if opened_count <= 0:
            return 0
        multiplier = cls.visible_multiplier(opened_count)
        base = bet * multiplier * HOUSE_PAYOUT_FACTOR
        base *= 1 - cls.rich_tax(balance_after_bet)
        base *= 1 + cls.streak_bonus_rate(streak, opened_count, bet)
        payout = floor(base)
        if opened_count == 1:
            payout = max(payout, bet)
        return max(0, min(MAX_PAYOUT, payout))


class MinesRenderer:
    @staticmethod
    def keyboard(game: GambleMinesGame, reveal: bool = False) -> InlineKeyboardMarkup:
        mines = _loads_int_set(game.mines_json)
        opened = _loads_int_set(game.opened_json)
        rows: list[list[InlineKeyboardButton]] = []
        for row_idx in range(GRID_WIDTH):
            row: list[InlineKeyboardButton] = []
            for col_idx in range(GRID_WIDTH):
                cell = row_idx * GRID_WIDTH + col_idx
                if cell in opened:
                    text = "💎"
                    callback = f"gm:noop:{game.id}:{game.token}:{cell}"
                elif reveal and cell in mines:
                    text = "💣"
                    callback = f"gm:noop:{game.id}:{game.token}:{cell}"
                elif reveal:
                    text = "▫️"
                    callback = f"gm:noop:{game.id}:{game.token}:{cell}"
                else:
                    text = "⬜"
                    callback = f"gm:o:{game.id}:{game.token}:{cell}"
                row.append(InlineKeyboardButton(text=text, callback_data=callback))
            rows.append(row)
        if game.status == "active":
            rows.append([InlineKeyboardButton(text="💰 Pulni olish", callback_data=f"gm:c:{game.id}:{game.token}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def text(game: GambleMinesGame, *, balance: int = 0, streak: int = 0, result: str = "") -> str:
        opened_count = len(_loads_int_set(game.opened_json))
        multiplier = MinesMath.visible_multiplier(opened_count)
        potential = int(game.payout or 0)
        mine_hint = max(1, int(game.mine_count or 5))
        bonus_rate = MinesMath.streak_bonus_rate(streak, opened_count, int(game.bet))
        rich_tax = MinesMath.rich_tax(balance)
        economy_line = ""
        if bonus_rate > 0:
            economy_line += f"{_ce('🎁', GIFT_EMOJI_ID)} Streak bonus: <b>+{bonus_rate * 100:.1f}%</b>\n"
        if rich_tax > 0:
            economy_line += f"{_ce('🏦', BANK_EMOJI_ID)} Rich tax: <b>-{rich_tax * 100:.0f}%</b>\n"
        status_line = {
            "active": "🎲 <b>Qimor: Mines</b>",
            "lost": "💥 <b>Mina portladi!</b>",
            "cashed": "✅ <b>Pul olindi!</b>",
        }.get(game.status, "🎲 <b>Qimor: Mines</b>")
        footer = result or (
            "Katak tanlang yoki yutuqni vaqtida oling."
            if opened_count
            else "36 ta katakdan birini tanlang. Mina tushsa stavka kuyadi."
        )
        return (
            f"{status_line}\n"
            "━━━━━━━━━━━━━━━\n"
            f"{_ce('💰', MONEY_EMOJI_ID)} Stavka: <b>{int(game.bet)}</b> dollar\n"
            f"📈 Multiplier: <b>x{multiplier:.2f}</b>\n"
            f"{_ce('💰', MONEY_EMOJI_ID)} Hozirgi yutuq: <b>{potential}</b> dollar\n"
            f"{_ce('💎', DIAMOND_EMOJI_ID)} Ochilgan safe: <b>{opened_count}</b> ta\n"
            f"{_ce('💣', MINE_EMOJI_ID)} Qolgan mina taxmini: <b>{mine_hint}</b> ta\n"
            f"🔥 Streak: <b>{int(streak)}</b>\n"
            f"{economy_line}"
            f"{_ce('🏦', BANK_EMOJI_ID)} Balans: <b>{int(balance)}</b> dollar\n"
            "━━━━━━━━━━━━━━━\n"
            f"{footer}"
        )

    @staticmethod
    def final_text(game: GambleMinesGame) -> str:
        if game.status == "cashed":
            return f"{_ce('💰', MONEY_EMOJI_ID)} <b>{int(game.payout or 0)}</b> dollar yutdingiz!"
        if game.status == "lost":
            return f"{_ce('💣', MINE_EMOJI_ID)} Mina portladi!\n{_ce('💰', MONEY_EMOJI_ID)} <b>{int(game.bet or 0)}</b> dollar kuyib ketdi."
        return MinesRenderer.text(game)


class MinesEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def start_or_resume(self, tg_user, chat_id: int, raw_amount: str | None) -> MinesView:
        async with self.session_factory() as session:
            user = await self._ensure_user(session, tg_user)
            active = await self._active_game(session, tg_user.id)
            stats = await self._stats(session, tg_user.id)
            if active is not None:
                active.chat_id = chat_id
                active.payout = MinesMath.payout(
                    int(active.bet), len(_loads_int_set(active.opened_json)), int(user.dollar or 0), int(stats.win_streak or 0)
                )
                active.last_action_at = _utcnow()
                await session.commit()
                return MinesView(
                    MinesRenderer.text(active, balance=int(user.dollar or 0), streak=int(stats.win_streak or 0), result="Davom etayotgan o'yiningiz tiklandi."),
                    MinesRenderer.keyboard(active),
                    "Davom etayotgan o'yin ochildi.",
                    False,
                    int(active.id),
                    active.token,
                )

            ok, bet, error = MinesAntiCheatValidator.validate_bet(raw_amount)
            if not ok:
                return MinesView(error, None, error, True)

            now = _utcnow()
            if stats.last_started_at and _ensure_aware(stats.last_started_at) + timedelta(seconds=COOLDOWN_SECONDS) > now:
                wait = int((_ensure_aware(stats.last_started_at) + timedelta(seconds=COOLDOWN_SECONDS) - now).total_seconds())
                return MinesView(f"⏳ Keyingi qimorgacha <b>{wait}</b> sekund kuting.", None, "Cooldown hali tugamadi.", True)

            if int(user.dollar or 0) < bet:
                return MinesView("Balans yetarli emas.", None, "Balans yetarli emas.", True)

            user.dollar = int(user.dollar or 0) - bet
            stats.last_started_at = now
            stats.total_bet = int(stats.total_bet or 0) + bet
            mine_count = random.choices([4, 5, 6], weights=[20, 40, 40], k=1)[0]
            mines = sorted(secrets.SystemRandom().sample(range(GRID_SIZE), mine_count))
            game = GambleMinesGame(
                user_telegram_id=tg_user.id,
                chat_id=chat_id,
                bet=bet,
                mine_count=mine_count,
                mines_json=json.dumps(mines),
                opened_json="[]",
                status="active",
                multiplier=1.0,
                payout=0,
                token=secrets.token_hex(5),
                last_action_at=now,
            )
            session.add(game)
            await session.flush()
            MinesEconomyService.record_dollar(
                session,
                user,
                -bet,
                "gamble_mines_bet",
                note=f"Mines stavka #{game.id}",
                chat_id=chat_id,
            )
            await session.commit()
            return MinesView(
                MinesRenderer.text(game, balance=int(user.dollar or 0), streak=int(stats.win_streak or 0)),
                MinesRenderer.keyboard(game),
                game_id=int(game.id),
                token=game.token,
            )

    async def set_message_id(self, game_id: int, token: str, message_id: int) -> None:
        async with self.session_factory() as session:
            game = (
                await session.execute(
                    select(GambleMinesGame).where(GambleMinesGame.id == game_id, GambleMinesGame.token == token)
                )
            ).scalar_one_or_none()
            if game and game.message_id is None:
                game.message_id = message_id
                await session.commit()
            elif game and game.message_id != message_id:
                game.message_id = message_id
                await session.commit()

    async def open_cell(self, tg_user_id: int, game_id: int, token: str, cell: int) -> MinesView:
        if cell < 0 or cell >= GRID_SIZE:
            return MinesView("", None, "Katak noto'g'ri.", True)
        lock = _game_lock(game_id)
        await lock.acquire()
        try:
            async with self.session_factory() as session:
                game = await self._game_for_update(session, game_id, token)
                if game is None:
                    return MinesView("", None, "O'yin topilmadi yoki eskirgan.", True)
                if game.user_telegram_id != tg_user_id:
                    return MinesView("", None, "Bu boshqa o'yinchining qimori.", True)
                user = await self._user(session, tg_user_id)
                stats = await self._stats(session, tg_user_id)
                if game.status != "active":
                    return MinesView(
                        MinesRenderer.final_text(game),
                        None,
                        "Bu o'yin yakunlangan.",
                        True,
                    )
                mines = _loads_int_set(game.mines_json)
                opened = _loads_int_set(game.opened_json)
                if cell in opened:
                    return MinesView("", None, "Bu katak allaqachon ochilgan.", True)

                now = _utcnow()
                game.last_action_at = now
                if cell in mines:
                    game.status = "lost"
                    game.ended_at = now
                    game.payout = 0
                    stats.win_streak = 0
                    await session.commit()
                    return MinesView(
                        MinesRenderer.final_text(game),
                        None,
                        "💣 Mina! Stavka kuyib ketdi.",
                        True,
                    )

                opened.add(cell)
                opened_count = len(opened)
                game.opened_json = json.dumps(sorted(opened))
                game.multiplier = MinesMath.visible_multiplier(opened_count)
                game.payout = MinesMath.payout(int(game.bet), opened_count, int(user.dollar if user else 0), int(stats.win_streak or 0))
                await session.commit()
                balance = int(user.dollar if user else 0)
                return MinesView(
                    MinesRenderer.text(game, balance=balance, streak=int(stats.win_streak or 0), result="Safe ochildi. Davom etish yoki pulni olish sizning qo'lingizda."),
                    MinesRenderer.keyboard(game),
                    "💎 Safe!",
                    False,
                )
        finally:
            lock.release()

    async def cashout(self, tg_user_id: int, game_id: int, token: str) -> MinesView:
        lock = _game_lock(game_id)
        await lock.acquire()
        try:
            async with self.session_factory() as session:
                game = await self._game_for_update(session, game_id, token)
                if game is None:
                    return MinesView("", None, "O'yin topilmadi yoki eskirgan.", True)
                if game.user_telegram_id != tg_user_id:
                    return MinesView("", None, "Bu boshqa o'yinchining qimori.", True)
                user = await self._user(session, tg_user_id)
                stats = await self._stats(session, tg_user_id)
                if user is None:
                    return MinesView("", None, "Foydalanuvchi topilmadi.", True)
                if game.status != "active":
                    return MinesView(
                        MinesRenderer.final_text(game),
                        None,
                        "Bu o'yin yakunlangan.",
                        True,
                    )
                opened_count = len(_loads_int_set(game.opened_json))
                if opened_count <= 0:
                    return MinesView("", None, "Avval kamida bitta safe katak oching.", True)
                payout = MinesMath.payout(int(game.bet), opened_count, int(user.dollar or 0), int(stats.win_streak or 0))
                if payout <= 0:
                    return MinesView("", None, "Yutuq mavjud emas.", True)
                now = _utcnow()
                today_won = await self._today_won(session, tg_user_id, now)
                if today_won >= DAILY_WIN_LIMIT:
                    return MinesView("", None, "Kunlik yutuq limiti tugagan: 50000 dollar.", True)
                if today_won + payout > DAILY_WIN_LIMIT:
                    payout = DAILY_WIN_LIMIT - today_won
                    if payout <= 0:
                        return MinesView("", None, "Kunlik yutuq limiti tugagan: 50000 dollar.", True)
                user.dollar = int(user.dollar or 0) + payout
                game.status = "cashed"
                game.payout = payout
                game.ended_at = now
                game.last_action_at = now
                stats.win_streak = int(stats.win_streak or 0) + 1
                stats.total_payout = int(stats.total_payout or 0) + payout
                MinesEconomyService.record_dollar(
                    session,
                    user,
                    payout,
                    "gamble_mines_cashout",
                    note=f"Mines cashout #{game.id}: {opened_count} safe, x{game.multiplier:.2f}",
                    chat_id=game.chat_id,
                )
                await session.commit()
                return MinesView(
                    MinesRenderer.final_text(game),
                    None,
                    f"💰 {payout} dollar olindi!",
                    True,
                )
        finally:
            lock.release()

    async def weekly_top_text(self, limit: int = 10) -> str:
        now = _utcnow()
        since = now - timedelta(days=7)
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        GambleMinesGame.user_telegram_id,
                        func.coalesce(func.sum(GambleMinesGame.payout), 0).label("total_won"),
                        func.count(GambleMinesGame.id).label("games_count"),
                        func.coalesce(func.max(GambleMinesGame.payout), 0).label("best_win"),
                    )
                    .where(
                        GambleMinesGame.status == "cashed",
                        GambleMinesGame.payout > 0,
                        GambleMinesGame.ended_at.is_not(None),
                        GambleMinesGame.ended_at >= since,
                    )
                    .group_by(GambleMinesGame.user_telegram_id)
                    .order_by(desc("total_won"), desc("best_win"))
                    .limit(max(1, min(30, int(limit))))
                )
            ).all()
            users = {}
            if rows:
                user_ids = [int(row.user_telegram_id) for row in rows]
                users = {
                    user.telegram_id: user
                    for user in (
                        await session.execute(select(User).where(User.telegram_id.in_(user_ids)))
                    ).scalars().all()
                }

        if not rows:
            return (
                "🏆 <b>Haftalik qimorvozlar TOP</b>\n"
                "━━━━━━━━━━━━━━━\n"
                "Hali bu haftada yutuq olgan qimorvoz yo'q.\n\n"
                f"Faqat safe katak ochib, <b>{_ce('💰', MONEY_EMOJI_ID)} Pulni olish</b> orqali cashout qilingan yutuqlar statistikaga kiradi."
            )

        lines: list[str] = []
        for idx, row in enumerate(rows, 1):
            telegram_id = int(row.user_telegram_id)
            user = users.get(telegram_id)
            name = user.display_name if user else str(telegram_id)
            total_won = int(row.total_won or 0)
            games_count = int(row.games_count or 0)
            best_win = int(row.best_win or 0)
            lines.append(
                f"{idx}. {_user_link(telegram_id, name)} — <b>{total_won}</b> {_ce('💰', MONEY_EMOJI_ID)}\n"
                f"   🎮 Cashout: <b>{games_count}</b> | 🔥 Eng katta: <b>{best_win}</b>"
            )

        return (
            "🏆 <b>Haftalik eng yaxshi qimorvozlar</b>\n"
            "━━━━━━━━━━━━━━━\n"
            + "\n".join(lines)
            + "\n━━━━━━━━━━━━━━━\n"
            "📌 Hisob: oxirgi 7 kun ichida cashout qilingan umumiy yutuq.\n"
            "⚠️ Katak ochilmasdan bosilgan pul olish statistikaga kirmaydi."
        )

    async def _ensure_user(self, session: AsyncSession, tg_user) -> User:
        user = (await session.execute(select(User).where(User.telegram_id == tg_user.id))).scalar_one_or_none()
        if user is None:
            user = User(
                telegram_id=tg_user.id,
                username=getattr(tg_user, "username", None),
                display_name=(getattr(tg_user, "full_name", None) or "User")[:255],
                language="uz",
            )
            session.add(user)
            await session.flush()
        else:
            user.username = getattr(tg_user, "username", None)
            user.display_name = (getattr(tg_user, "full_name", None) or user.display_name or "User")[:255]
        return user

    async def _user(self, session: AsyncSession, telegram_id: int) -> Optional[User]:
        return (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()

    async def _stats(self, session: AsyncSession, telegram_id: int) -> GambleUserStats:
        stats = (
            await session.execute(select(GambleUserStats).where(GambleUserStats.user_telegram_id == telegram_id))
        ).scalar_one_or_none()
        if stats is None:
            stats = GambleUserStats(user_telegram_id=telegram_id)
            session.add(stats)
            await session.flush()
        return stats

    async def _active_game(self, session: AsyncSession, telegram_id: int) -> Optional[GambleMinesGame]:
        return (
            await session.execute(
                select(GambleMinesGame)
                .where(GambleMinesGame.user_telegram_id == telegram_id, GambleMinesGame.status == "active")
                .order_by(GambleMinesGame.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _game_for_update(self, session: AsyncSession, game_id: int, token: str) -> Optional[GambleMinesGame]:
        return (
            await session.execute(
                select(GambleMinesGame)
                .where(GambleMinesGame.id == game_id, GambleMinesGame.token == token)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _today_won(self, session: AsyncSession, telegram_id: int, now: datetime) -> int:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        value = await session.scalar(
            select(func.coalesce(func.sum(GambleMinesGame.payout), 0)).where(
                GambleMinesGame.user_telegram_id == telegram_id,
                GambleMinesGame.status == "cashed",
                GambleMinesGame.ended_at.is_not(None),
                GambleMinesGame.ended_at >= start,
            )
        )
        return int(value or 0)


def _loads_int_set(raw: str) -> set[int]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(parsed, list):
        return set()
    result: set[int] = set()
    for item in parsed:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= value < GRID_SIZE:
            result.add(value)
    return result


def _user_link(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(name or str(user_id))}</a>'


def _game_lock(game_id: int) -> asyncio.Lock:
    lock = _GAME_LOCKS.get(game_id)
    if lock is None:
        lock = asyncio.Lock()
        _GAME_LOCKS[game_id] = lock
    return lock


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
