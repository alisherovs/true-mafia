from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ChickenRoadSession, DollarTransaction, GameHistory, User

logger = logging.getLogger(__name__)

CHICKEN_STEPS = 8
CHICKEN_MIN_BET = 100
CHICKEN_MAX_BET = 100_000
CHICKEN_BET_OPTIONS = (100, 500, 1000, 5000, 10000)
CHICKEN_GAME_TYPE = "chicken_road"
CHICKEN_ACTIVE = "active"
CHICKEN_STATUSES = {"active", "won", "lost", "cashed_out", "cancelled"}
CHICKEN_MULTIPLIERS = {
    1: 1.10,
    2: 1.35,
    3: 1.70,
    4: 2.20,
    5: 2.90,
    6: 3.90,
    7: 5.50,
    8: 8.50,
}
EASY_DANGER_COUNT = 3
MEDIUM_DANGER_COUNT = 4
HARD_DANGER_COUNT = 5
CHICKEN_DANGER_COUNTS = {
    "easy": EASY_DANGER_COUNT,
    "medium": MEDIUM_DANGER_COUNT,
    "hard": HARD_DANGER_COUNT,
}
CHICKEN_SEPARATOR = "━━━━━━━━━━━━━━━"
CHICKEN_MONEY_EMOJI_ID = "5409048419211682843"
CHICKEN_MINE_EMOJI_ID = "5469654973308476699"
CHICKEN_LOCKS: dict[int, asyncio.Lock] = {}
CHICKEN_USER_LOCKS: dict[int, asyncio.Lock] = {}


@dataclass(frozen=True)
class ChickenView:
    text: str
    keyboard: Optional[InlineKeyboardMarkup] = None
    alert: str = ""
    show_alert: bool = False
    session_id: Optional[int] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ce(symbol: str, emoji_id: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{symbol}</tg-emoji>'


def _lock(session_id: int) -> asyncio.Lock:
    lock = CHICKEN_LOCKS.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        CHICKEN_LOCKS[session_id] = lock
    return lock


def _user_lock(user_id: int) -> asyncio.Lock:
    lock = CHICKEN_USER_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        CHICKEN_USER_LOCKS[user_id] = lock
    return lock


def _json_loads(raw: str | None, fallback):
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def generate_road_map(difficulty: str = "easy") -> list[str]:
    difficulty = (difficulty or "easy").lower()
    danger_count = CHICKEN_DANGER_COUNTS.get(difficulty, EASY_DANGER_COUNT)
    road = ["safe"] * CHICKEN_STEPS
    danger_steps = secrets.SystemRandom().sample(range(2, CHICKEN_STEPS + 1), danger_count)
    for step in danger_steps:
        road[step - 1] = "danger"
    return road


def calculate_chicken_multiplier(step: int) -> float:
    if step <= 0:
        return 1.0
    return CHICKEN_MULTIPLIERS.get(min(int(step), CHICKEN_STEPS), 1.0)


def calculate_chicken_win(bet_amount: int, multiplier: float) -> int:
    return max(0, int(int(bet_amount) * float(multiplier)))


def parse_chicken_callback(data: str) -> tuple[str, Optional[int], Optional[int]]:
    parts = (data or "").split(":")
    if len(parts) < 2:
        raise ValueError("bad_callback")
    if parts[0] == "qimor" and parts[1] == "chicken" and len(parts) == 3 and parts[2] == "start":
        return "menu", None, None
    if parts[0] != "chicken":
        raise ValueError("bad_callback")
    action = parts[1]
    if action == "back":
        owner_id = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
        return action, None, owner_id
    if action == "custom_bet":
        owner_id = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
        return action, None, owner_id
    if action == "bet" and len(parts) == 3 and parts[2].isdigit():
        return action, int(parts[2]), None
    if action == "bet" and len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
        return action, int(parts[3]), int(parts[2])
    if action in {"go", "cashout", "cancel"} and len(parts) == 3 and parts[2].isdigit():
        return action, int(parts[2]), None
    if action == "noop" and len(parts) == 3 and parts[2].isdigit():
        return action, int(parts[2]), None
    raise ValueError("bad_callback")


def chicken_start_text() -> str:
    return (
        f"{CHICKEN_SEPARATOR}\n"
        "🐔 <b>CHICKEN ROAD</b>\n"
        f"{CHICKEN_SEPARATOR}\n\n"
        "Yo'ldan xavfsiz o'ting.\n"
        "Har qadamda multiplikator oshadi.\n"
        "Xavfga tushsangiz stavka kuyadi.\n\n"
        "💰 <b>Stavkani tanlang</b>"
    )


def build_chicken_start_keyboard(owner_id: int | None = None) -> InlineKeyboardMarkup:
    def cb(action: str, value: int | None = None) -> str:
        if action == "bet" and value is not None:
            return f"chicken:bet:{owner_id}:{value}" if owner_id else f"chicken:bet:{value}"
        if action == "custom_bet":
            return f"chicken:custom_bet:{owner_id}" if owner_id else "chicken:custom_bet"
        if action == "back":
            return f"chicken:back:{owner_id}" if owner_id else "chicken:back"
        return "chicken:back"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="100 ⭐", callback_data=cb("bet", 100)),
                InlineKeyboardButton(text="500 ⭐", callback_data=cb("bet", 500)),
                InlineKeyboardButton(text="1000 ⭐", callback_data=cb("bet", 1000)),
            ],
            [
                InlineKeyboardButton(text="5000 ⭐", callback_data=cb("bet", 5000)),
                InlineKeyboardButton(text="10000 ⭐", callback_data=cb("bet", 10000)),
            ],
            [InlineKeyboardButton(text="✍️ Boshqa summa", callback_data=cb("custom_bet"))],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=cb("back"))],
        ]
    )


def build_chicken_active_keyboard(session_id: int) -> InlineKeyboardMarkup:
    placeholder = ChickenRoadSession(
        id=int(session_id),
        user_id=0,
        chat_id=0,
        bet_amount=0,
        current_step=0,
        current_multiplier=1.0,
        road_map="[]",
        status=CHICKEN_ACTIVE,
        win_amount=0,
    )
    road_row = _chicken_road_buttons(placeholder)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            road_row,
            [InlineKeyboardButton(text="🚶 Oldinga yurish", callback_data=f"chicken:go:{session_id}")],
            [InlineKeyboardButton(text="💰 Pulni olish", callback_data=f"chicken:cashout:{session_id}")],
            [InlineKeyboardButton(text="❌ Taslim bo'lish", callback_data=f"chicken:cancel:{session_id}")],
        ]
    )


def _chicken_cell_text(session: ChickenRoadSession, step: int) -> str:
    current_step = int(session.current_step or 0)
    road_map = _json_loads(session.road_map, [])
    index = step - 1
    is_danger = index < len(road_map) and road_map[index] == "danger"
    if session.status == "lost" and step == current_step and is_danger:
        return "💥"
    if current_step == 0 and step == 1 and session.status == CHICKEN_ACTIVE:
        return "🐔"
    if step == current_step and session.status == CHICKEN_ACTIVE:
        return "🐔"
    if step <= current_step:
        return "✅"
    return "⬜"


def _chicken_road_buttons(session: ChickenRoadSession) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text=_chicken_cell_text(session, step), callback_data=f"chicken:noop:{session.id}")
        for step in range(CHICKEN_STEPS, 0, -1)
    ]


def _build_chicken_keyboard_for_session(session: ChickenRoadSession) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _chicken_road_buttons(session),
            [InlineKeyboardButton(text="🚶 Oldinga yurish", callback_data=f"chicken:go:{session.id}")],
            [InlineKeyboardButton(text="💰 Pulni olish", callback_data=f"chicken:cashout:{session.id}")],
            [InlineKeyboardButton(text="❌ Taslim bo'lish", callback_data=f"chicken:cancel:{session.id}")],
        ]
    )


def render_chicken_board(session: ChickenRoadSession, reveal_danger: bool = False) -> str:
    current_step = int(session.current_step or 0)
    road_map = _json_loads(session.road_map, [])
    cells: list[str] = ["🏁"]
    for step in range(CHICKEN_STEPS, 0, -1):
        index = step - 1
        is_danger = index < len(road_map) and road_map[index] == "danger"
        if session.status == "lost" and step == current_step and is_danger:
            cells.append("💥")
        elif step == current_step and session.status == CHICKEN_ACTIVE:
            cells.append("🐔")
        elif step <= current_step:
            cells.append("✅")
        elif reveal_danger and is_danger:
            cells.append("💣")
        else:
            cells.append("⬜")
    if current_step == 0 and session.status == CHICKEN_ACTIVE:
        cells[-1] = "🐔"
    return " ".join(cells)


def render_chicken_text(session: ChickenRoadSession, user_balance: int, result: str = "") -> str:
    current_step = int(session.current_step or 0)
    multiplier = float(session.current_multiplier or 1.0)
    current_win = calculate_chicken_win(int(session.bet_amount), multiplier) if current_step > 0 else 0
    parts = [
        CHICKEN_SEPARATOR,
        "🐔 <b>CHICKEN ROAD</b>",
        CHICKEN_SEPARATOR,
        "",
        f"💰 Stavka: <b>{int(session.bet_amount)}</b> ⭐",
        f"🚶 Qadam: <b>{current_step}</b>/<b>{CHICKEN_STEPS}</b>",
        f"📈 Multiplikator: <b>x{multiplier:.2f}</b>",
        f"🏆 Hozirgi olish: <b>{current_win}</b> ⭐",
    ]
    if result:
        parts.extend(["", result])
    elif session.status == CHICKEN_ACTIVE:
        parts.extend(["", "Davom etasizmi?"])
    return "\n".join(parts)


def _history(session: ChickenRoadSession, result: str) -> GameHistory:
    return GameHistory(
        user_id=int(session.user_id),
        game_type=CHICKEN_GAME_TYPE,
        bet_amount=int(session.bet_amount),
        result=result,
        multiplier=float(session.current_multiplier or 1.0),
        win_amount=int(session.win_amount or 0),
    )


def _record_dollar(session: AsyncSession, user: User, amount: int, action: str, note: str, chat_id: int) -> None:
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
            chat_id=int(chat_id),
        )
    )


class ChickenRoadEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def start_chicken_game(self, tg_user: TelegramUser, chat_id: int, bet_amount: int) -> ChickenView:
        bet_amount = int(bet_amount)
        if bet_amount < CHICKEN_MIN_BET or bet_amount > CHICKEN_MAX_BET:
            return ChickenView(
                f"❌ Stavka <b>{CHICKEN_MIN_BET}</b> dan <b>{CHICKEN_MAX_BET}</b> coin gacha bo'lishi kerak.",
                build_chicken_start_keyboard(),
                "Stavka noto'g'ri.",
                True,
            )
        async with _user_lock(int(tg_user.id)):
            async with self.session_factory() as session:
                async with session.begin():
                    user = await self._get_or_create_user(session, tg_user)
                    active = await self._active_session(session, int(user.id))
                    if active is not None:
                        text = render_chicken_text(active, int(user.dollar or 0), "Davom etayotgan o'yiningiz tiklandi.")
                        return ChickenView(text, _build_chicken_keyboard_for_session(active), "Sizda aktiv o'yin bor.", True, int(active.id))
                    if int(user.dollar or 0) < bet_amount:
                        return ChickenView("❌ Balansingiz yetarli emas.", build_chicken_start_keyboard(), "Balans yetarli emas.", True)

                    user.dollar = max(0, int(user.dollar or 0) - bet_amount)
                    game = ChickenRoadSession(
                        user_id=int(user.id),
                        chat_id=int(chat_id),
                        bet_amount=bet_amount,
                        current_step=0,
                        current_multiplier=1.0,
                        difficulty="easy",
                        road_map=json.dumps(generate_road_map("easy"), ensure_ascii=False),
                        status=CHICKEN_ACTIVE,
                        win_amount=0,
                    )
                    session.add(game)
                    await session.flush()
                    _record_dollar(session, user, -bet_amount, "chicken_bet", f"Chicken Road stavka #{game.id}", chat_id)
                    logger.info("chicken_started user=%s session=%s bet=%s", tg_user.id, game.id, bet_amount)
                    return ChickenView(render_chicken_text(game, int(user.dollar or 0)), _build_chicken_keyboard_for_session(game), session_id=int(game.id))

    async def set_message_id(self, session_id: int, message_id: int) -> None:
        async with self.session_factory() as session:
            game = await session.get(ChickenRoadSession, int(session_id))
            if game is None:
                return
            game.message_id = int(message_id)
            await session.commit()

    async def handle_chicken_go(self, tg_user_id: int, session_id: int) -> ChickenView:
        lock = _lock(int(session_id))
        async with lock:
            async with self.session_factory() as session:
                async with session.begin():
                    game = await self._session_for_update(session, session_id)
                    if game is None:
                        return ChickenView("", None, "❌ O'yin topilmadi.", True)
                    user = await session.get(User, int(game.user_id))
                    if user is None:
                        return ChickenView("", None, "User topilmadi.", True)
                    guard = self._guard(game, user, tg_user_id)
                    if guard is not None:
                        return guard

                    next_step = int(game.current_step or 0) + 1
                    if next_step > CHICKEN_STEPS:
                        return ChickenView("", None, "❌ Bu o'yin yakunlangan.", True)
                    road_map = _json_loads(game.road_map, [])
                    danger = next_step - 1 < len(road_map) and road_map[next_step - 1] == "danger"
                    game.current_step = next_step
                    game.current_multiplier = calculate_chicken_multiplier(next_step)
                    game.updated_at = _utcnow()

                    if danger:
                        game.status = "lost"
                        game.win_amount = 0
                        session.add(_history(game, "lost"))
                        logger.info("chicken_lost user=%s session=%s step=%s", tg_user_id, session_id, next_step)
                        return ChickenView(
                            f"{_ce('💣', CHICKEN_MINE_EMOJI_ID)} <b>Tovuq yo'lda urildi!</b>\n"
                            f"{CHICKEN_SEPARATOR}\n"
                            f"{_ce('💵', CHICKEN_MONEY_EMOJI_ID)} <b>{int(game.bet_amount)}</b> dollar kuyib ketdi.\n\n"
                            f"{render_chicken_board(game)}\n"
                            f"{CHICKEN_SEPARATOR}",
                            None,
                            "💥 Xavfga tushdingiz!",
                            True,
                        )

                    if next_step >= CHICKEN_STEPS:
                        payout = calculate_chicken_win(int(game.bet_amount), CHICKEN_MULTIPLIERS[CHICKEN_STEPS])
                        user.dollar = int(user.dollar or 0) + payout
                        game.status = "won"
                        game.current_multiplier = CHICKEN_MULTIPLIERS[CHICKEN_STEPS]
                        game.win_amount = payout
                        session.add(_history(game, "won"))
                        _record_dollar(session, user, payout, "chicken_win", f"Chicken Road maksimal yutuq #{game.id}", int(game.chat_id))
                        logger.info("chicken_won user=%s session=%s payout=%s", tg_user_id, session_id, payout)
                        return ChickenView(
                            "🏆 <b>Qimor yakunlandi</b>\n"
                            f"{CHICKEN_SEPARATOR}\n"
                            f"{_ce('💵', CHICKEN_MONEY_EMOJI_ID)} Yutuq: <b>{payout}</b> dollar\n"
                            f"{CHICKEN_SEPARATOR}",
                            None,
                            "🏆 Maksimal yutuq!",
                            True,
                        )

                    return ChickenView(
                        render_chicken_text(game, int(user.dollar or 0), "✅ Qadam muvaffaqiyatli."),
                        _build_chicken_keyboard_for_session(game),
                        "✅ Safe!",
                    )

    async def handle_chicken_cashout(self, tg_user_id: int, session_id: int) -> ChickenView:
        lock = _lock(int(session_id))
        async with lock:
            async with self.session_factory() as session:
                async with session.begin():
                    game = await self._session_for_update(session, session_id)
                    if game is None:
                        return ChickenView("", None, "❌ O'yin topilmadi.", True)
                    user = await session.get(User, int(game.user_id))
                    if user is None:
                        return ChickenView("", None, "User topilmadi.", True)
                    guard = self._guard(game, user, tg_user_id)
                    if guard is not None:
                        return guard
                    if int(game.current_step or 0) <= 0:
                        return ChickenView("", _build_chicken_keyboard_for_session(game), "❌ Avval kamida bitta qadam yuring.", True)

                    payout = calculate_chicken_win(int(game.bet_amount), float(game.current_multiplier or 1.0))
                    user.dollar = int(user.dollar or 0) + payout
                    game.status = "cashed_out"
                    game.win_amount = payout
                    game.updated_at = _utcnow()
                    session.add(_history(game, "cashed_out"))
                    _record_dollar(session, user, payout, "chicken_cashout", f"Chicken Road cashout #{game.id}", int(game.chat_id))
                    logger.info("chicken_cashout user=%s session=%s payout=%s", tg_user_id, session_id, payout)
                    return ChickenView(
                        "🏆 <b>Qimor yakunlandi</b>\n"
                        f"{CHICKEN_SEPARATOR}\n"
                        f"{_ce('💵', CHICKEN_MONEY_EMOJI_ID)} Yutuq: <b>{payout}</b> dollar\n"
                        f"{CHICKEN_SEPARATOR}",
                        None,
                        f"💰 {payout} dollar olindi!",
                        True,
                    )

    async def handle_chicken_cancel(self, tg_user_id: int, session_id: int) -> ChickenView:
        lock = _lock(int(session_id))
        async with lock:
            async with self.session_factory() as session:
                async with session.begin():
                    game = await self._session_for_update(session, session_id)
                    if game is None:
                        return ChickenView("", None, "❌ O'yin topilmadi.", True)
                    user = await session.get(User, int(game.user_id))
                    if user is None:
                        return ChickenView("", None, "User topilmadi.", True)
                    guard = self._guard(game, user, tg_user_id)
                    if guard is not None:
                        return guard
                    game.status = "cancelled"
                    game.win_amount = 0
                    game.updated_at = _utcnow()
                    session.add(_history(game, "cancelled"))
                    logger.info("chicken_cancelled user=%s session=%s", tg_user_id, session_id)
                    return ChickenView(
                        f"{CHICKEN_SEPARATOR}\n"
                        "❌ <b>O'YIN YAKUNLANDI</b>\n"
                        f"{CHICKEN_SEPARATOR}\n\n"
                        f"💰 Stavka: <b>{int(game.bet_amount)}</b> ⭐\n"
                        "💸 Stavka qaytarilmaydi.",
                        None,
                        "O'yin bekor qilindi.",
                        True,
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

    async def _active_session(self, session: AsyncSession, user_id: int) -> Optional[ChickenRoadSession]:
        return await session.scalar(
            select(ChickenRoadSession)
            .where(ChickenRoadSession.user_id == int(user_id), ChickenRoadSession.status == CHICKEN_ACTIVE)
            .order_by(ChickenRoadSession.id.desc())
            .with_for_update()
        )

    async def _session_for_update(self, session: AsyncSession, session_id: int) -> Optional[ChickenRoadSession]:
        return await session.scalar(
            select(ChickenRoadSession).where(ChickenRoadSession.id == int(session_id)).with_for_update()
        )

    def _guard(self, game: ChickenRoadSession, user: User, tg_user_id: int) -> Optional[ChickenView]:
        if int(user.telegram_id) != int(tg_user_id):
            return ChickenView("", None, "❌ Bu o'yin sizniki emas.", True)
        if game.status != CHICKEN_ACTIVE:
            return ChickenView("", None, "❌ Bu o'yin yakunlangan.", True)
        if game.status not in CHICKEN_STATUSES:
            return ChickenView("", None, "❌ O'yin holati noto'g'ri.", True)
        return None
