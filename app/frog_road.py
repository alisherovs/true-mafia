from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import DollarTransaction, FrogGameSession, GameHistory, User

logger = logging.getLogger(__name__)

FROG_ROWS = 8
FROG_COLUMNS = 5
FROG_MIN_BET = 100
FROG_MAX_BET = 100_000
FROG_BET_OPTIONS = (100, 500, 1000, 5000, 10000)
FROG_GAME_TYPE = "frog"
FROG_ACTIVE = "active"
FROG_STATUSES = {"active", "won", "lost", "cashed_out", "cancelled"}
FROG_MULTIPLIERS = (1.18, 1.48, 1.85, 2.31, 2.89, 3.62, 4.52, 5.65)
FROG_DANGER_COUNTS = (1, 1, 2, 2, 3, 3, 4, 4)
FROG_LOCKS: dict[int, asyncio.Lock] = {}


@dataclass(frozen=True)
class FrogView:
    text: str
    keyboard: Optional[InlineKeyboardMarkup] = None
    alert: str = ""
    show_alert: bool = False
    session_id: Optional[int] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lock(session_id: int) -> asyncio.Lock:
    lock = FROG_LOCKS.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        FROG_LOCKS[session_id] = lock
    return lock


def _json_loads(raw: str | None, fallback):
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _user_link(user: User) -> str:
    name = escape(user.display_name or user.username or "User")
    return f'<a href="tg://user?id={int(user.telegram_id)}">{name}</a>'


def _danger_count_for_row(row: int) -> int:
    if 0 <= row < len(FROG_DANGER_COUNTS):
        return min(FROG_DANGER_COUNTS[row], FROG_COLUMNS - 1)
    return 1


def _danger_columns_for_row(danger_map: dict, row: int) -> set[int]:
    raw = danger_map.get(str(row), [])
    if isinstance(raw, int):
        raw = [raw]
    if not isinstance(raw, list):
        return set()
    result: set[int] = set()
    for item in raw:
        try:
            column = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= column < FROG_COLUMNS:
            result.add(column)
    return result


def generate_danger_map(rows: int = FROG_ROWS, columns: int = FROG_COLUMNS) -> dict[str, list[int]]:
    rng = secrets.SystemRandom()
    result: dict[str, list[int]] = {}
    for row in range(rows):
        count = min(_danger_count_for_row(row), max(1, columns - 1))
        result[str(row)] = sorted(rng.sample(range(columns), count))
    return result


def calculate_multiplier(row: int) -> float:
    if row <= 0:
        return 1.0
    index = min(max(row, 1), FROG_ROWS) - 1
    return FROG_MULTIPLIERS[index]


def calculate_win_amount(bet: int, multiplier: float) -> int:
    return max(0, int(int(bet) * float(multiplier)))


def parse_frog_callback(data: str) -> tuple[str, Optional[int], Optional[int], Optional[int]]:
    parts = (data or "").split(":")
    if len(parts) < 2 or parts[0] != "frog":
        raise ValueError("bad_callback")
    action = parts[1]
    if action == "custom_bet":
        owner_id = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
        return action, None, None, owner_id
    if action == "menu_cancel":
        owner_id = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
        return action, None, None, owner_id
    if action == "start" and len(parts) == 3 and parts[2].isdigit():
        return action, int(parts[2]), None, None
    if action == "start" and len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
        return action, int(parts[3]), None, int(parts[2])
    if action in {"cashout", "cancel"} and len(parts) == 3 and parts[2].isdigit():
        return action, int(parts[2]), None, None
    if action == "noop" and len(parts) == 3 and parts[2].isdigit():
        return action, int(parts[2]), None, None
    if action == "jump" and len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
        return action, int(parts[2]), int(parts[3]), None
    raise ValueError("bad_callback")


def frog_start_text() -> str:
    return (
        "━━━━━━━━━━━━━━━━━━\n"
        "🐸 <b>FROG RUSH</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "💎 Premium casino o'yini\n"
        "⚫ 5x8 yo'l  •  🟢 xavfsiz  •  💣 tuzoq\n\n"
        "💰 <b>Stavkani tanlang</b>"
    )


def build_frog_start_keyboard(owner_id: int | None = None) -> InlineKeyboardMarkup:
    def cb(action: str, value: int | None = None) -> str:
        if action == "start" and value is not None:
            return f"frog:start:{owner_id}:{value}" if owner_id else f"frog:start:{value}"
        if action == "custom_bet":
            return f"frog:custom_bet:{owner_id}" if owner_id else "frog:custom_bet"
        if action == "menu_cancel":
            return f"frog:menu_cancel:{owner_id}" if owner_id else "frog:menu_cancel"
        return "frog:menu_cancel"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="100 ⭐", callback_data=cb("start", 100)),
                InlineKeyboardButton(text="500 ⭐", callback_data=cb("start", 500)),
                InlineKeyboardButton(text="1000 ⭐", callback_data=cb("start", 1000)),
            ],
            [
                InlineKeyboardButton(text="5000 ⭐", callback_data=cb("start", 5000)),
                InlineKeyboardButton(text="10000 ⭐", callback_data=cb("start", 10000)),
            ],
            [InlineKeyboardButton(text="✍️ Boshqa summa", callback_data=cb("custom_bet"))],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=cb("menu_cancel"))],
        ]
    )


def _frog_board_state(session: FrogGameSession) -> tuple[set[tuple[int, int]], set[tuple[int, int]], Optional[tuple[int, int]]]:
    opened = _json_loads(session.opened_cells, [])
    current_position = _json_loads(session.current_position, {})
    opened_safe = {
        (int(item.get("row")), int(item.get("column")))
        for item in opened
        if isinstance(item, dict) and item.get("safe") is True
    }
    opened_danger = {
        (int(item.get("row")), int(item.get("column")))
        for item in opened
        if isinstance(item, dict) and item.get("safe") is False
    }
    position = None
    if isinstance(current_position, dict) and {"row", "column"} <= set(current_position):
        try:
            position = (int(current_position["row"]), int(current_position["column"]))
        except (TypeError, ValueError):
            position = None
    return opened_safe, opened_danger, position


def _frog_tile_text(
    point: tuple[int, int],
    opened_safe: set[tuple[int, int]],
    opened_danger: set[tuple[int, int]],
    position: Optional[tuple[int, int]],
) -> str:
    if point in opened_danger:
        return "💥"
    if position == point:
        return "🐸"
    if point in opened_safe:
        return "🟩"
    return "⬛"


def build_frog_keyboard(session: FrogGameSession) -> InlineKeyboardMarkup:
    opened_safe, opened_danger, position = _frog_board_state(session)
    rows: list[list[InlineKeyboardButton]] = []
    current_row = int(session.current_row or 0)
    active = session.status == FROG_ACTIVE
    for row in range(FROG_ROWS - 1, -1, -1):
        buttons: list[InlineKeyboardButton] = []
        for column in range(FROG_COLUMNS):
            point = (row, column)
            text = _frog_tile_text(point, opened_safe, opened_danger, position)
            callback_data = f"frog:jump:{session.id}:{column}" if active and row == current_row else f"frog:noop:{session.id}"
            buttons.append(InlineKeyboardButton(text=text, callback_data=callback_data))
        rows.append(buttons)
    if active:
        rows.append([InlineKeyboardButton(text="💰 Pulni olish", callback_data=f"frog:cashout:{session.id}")])
        rows.append([InlineKeyboardButton(text="❌ Taslim bo'lish", callback_data=f"frog:cancel:{session.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_frog_board(session: FrogGameSession, reveal_danger: bool = False) -> str:
    opened_safe, opened_danger, position = _frog_board_state(session)
    lines: list[str] = []
    for row in range(FROG_ROWS - 1, -1, -1):
        cells: list[str] = []
        for column in range(FROG_COLUMNS):
            point = (row, column)
            cells.append(_frog_tile_text(point, opened_safe, opened_danger, position))
        lines.append(" ".join(cells))
    return "\n".join(lines)


def render_frog_text(session: FrogGameSession, user_balance: int, result: str = "") -> str:
    current_row = int(session.current_row or 0)
    multiplier = float(session.current_multiplier or 1.0)
    current_win = calculate_win_amount(int(session.bet_amount), multiplier) if current_row > 0 else 0
    danger_count = _danger_count_for_row(current_row) if current_row < FROG_ROWS else _danger_count_for_row(FROG_ROWS - 1)
    parts = [
        "━━━━━━━━━━━━━━━━━━",
        "🐸 <b>FROG RUSH</b>",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"💰 Stavka: <b>{int(session.bet_amount)}</b> ⭐",
        f"📈 Multiplikator: <b>x{multiplier:.2f}</b>",
        f"🏆 Yutuq: <b>{current_win}</b> ⭐",
        f"🟢 Xavfsiz sakrash: <b>{current_row}</b>/<b>{FROG_ROWS}</b>",
        f"💣 Tuzoqlar: <b>{danger_count}</b>",
        "",
        "━━━━━━━━━━━━━━━━━━",
    ]
    if result:
        parts.extend(["", result])
    elif session.status == FROG_ACTIVE:
        parts.extend(["", "Keyingi platformani tanlang."])
    return "\n".join(parts)


def _history(session: FrogGameSession, result: str) -> GameHistory:
    return GameHistory(
        user_id=int(session.user_id),
        game_type=FROG_GAME_TYPE,
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
            chat_id=chat_id,
        )
    )


class FrogRoadEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def menu(self) -> FrogView:
        return FrogView(frog_start_text(), build_frog_start_keyboard())

    async def start_frog_game(self, tg_user: TelegramUser, chat_id: int, bet_amount: int) -> FrogView:
        bet_amount = int(bet_amount)
        if bet_amount < FROG_MIN_BET or bet_amount > FROG_MAX_BET:
            return FrogView(
                f"❌ Stavka <b>{FROG_MIN_BET}</b> dan <b>{FROG_MAX_BET}</b> coin gacha bo'lishi kerak.",
                build_frog_start_keyboard(),
                "Stavka noto'g'ri.",
                True,
            )
        async with self.session_factory() as session:
            async with session.begin():
                user = await self._get_or_create_user(session, tg_user)
                active = await self._active_session(session, int(user.id))
                if active is not None:
                    text = render_frog_text(active, int(user.dollar or 0), "Davom etayotgan o'yiningiz tiklandi.")
                    return FrogView(text, build_frog_keyboard(active), "Sizda aktiv o'yin bor.", True, int(active.id))
                if int(user.dollar or 0) < bet_amount:
                    return FrogView("❌ Balansingiz yetarli emas.", build_frog_start_keyboard(), "Balans yetarli emas.", True)

                user.dollar = max(0, int(user.dollar or 0) - bet_amount)
                game = FrogGameSession(
                    user_id=int(user.id),
                    chat_id=int(chat_id),
                    bet_amount=bet_amount,
                    current_row=0,
                    current_multiplier=1.0,
                    status=FROG_ACTIVE,
                    danger_map=json.dumps(generate_danger_map(), ensure_ascii=False),
                    opened_cells="[]",
                    current_position="{}",
                    win_amount=0,
                )
                session.add(game)
                await session.flush()
                _record_dollar(session, user, -bet_amount, "frog_bet", f"Qurbaqa Yo'li stavka #{game.id}", chat_id)
                logger.info("frog_started user=%s session=%s bet=%s", tg_user.id, game.id, bet_amount)
                return FrogView(render_frog_text(game, int(user.dollar or 0)), build_frog_keyboard(game), session_id=int(game.id))

    async def set_message_id(self, session_id: int, message_id: int) -> None:
        async with self.session_factory() as session:
            game = await session.get(FrogGameSession, int(session_id))
            if game is None:
                return
            game.message_id = int(message_id)
            await session.commit()

    async def handle_frog_jump(self, tg_user_id: int, session_id: int, column: int) -> FrogView:
        if column < 0 or column >= FROG_COLUMNS:
            return FrogView("", None, "Katak noto'g'ri.", True)
        lock = _lock(int(session_id))
        async with lock:
            async with self.session_factory() as session:
                async with session.begin():
                    game = await self._session_for_update(session, session_id)
                    if game is None:
                        return FrogView("", None, "O'yin topilmadi.", True)
                    user = await session.get(User, int(game.user_id))
                    if user is None:
                        return FrogView("", None, "User topilmadi.", True)
                    guard = self._guard(game, user, tg_user_id)
                    if guard is not None:
                        return guard

                    row = int(game.current_row or 0)
                    danger_map = _json_loads(game.danger_map, {})
                    danger_columns = _danger_columns_for_row(danger_map, row)
                    opened = _json_loads(game.opened_cells, [])
                    now = _utcnow()
                    game.updated_at = now

                    if column in danger_columns:
                        opened.append({"row": row, "column": column, "safe": False})
                        game.opened_cells = json.dumps(opened, ensure_ascii=False)
                        game.current_position = json.dumps({"row": row, "column": column}, ensure_ascii=False)
                        game.status = "lost"
                        game.win_amount = 0
                        session.add(_history(game, "lost"))
                        logger.info("frog_lost user=%s session=%s row=%s column=%s", tg_user_id, session_id, row, column)
                        text = (
                            "━━━━━━━━━━━━━━━━━━\n"
                            "💥 <b>YUTQAZDINGIZ</b>\n"
                            "━━━━━━━━━━━━━━━━━━\n\n"
                            "🐸 Qurbaqa tuzoqqa tushib qoldi.\n\n"
                            f"💰 Stavka: <b>{int(game.bet_amount)}</b> ⭐\n"
                            f"🟢 Sakrash: <b>{row}</b>/<b>{FROG_ROWS}</b>\n"
                            "💣 Mina portladi.\n"
                            "💸 Stavka kuyib ketdi.\n\n"
                            "━━━━━━━━━━━━━━━━━━"
                        )
                        return FrogView(text, build_frog_keyboard(game), "💥 Xavfli katakka tushdingiz!", True)

                    opened.append({"row": row, "column": column, "safe": True})
                    game.opened_cells = json.dumps(opened, ensure_ascii=False)
                    game.current_position = json.dumps({"row": row, "column": column}, ensure_ascii=False)
                    game.current_row = row + 1
                    game.current_multiplier = calculate_multiplier(int(game.current_row))
                    logger.info(
                        "frog_safe user=%s session=%s row=%s column=%s next_row=%s multiplier=%.2f",
                        tg_user_id,
                        session_id,
                        row,
                        column,
                        int(game.current_row),
                        float(game.current_multiplier or 1.0),
                    )

                    if int(game.current_row) >= FROG_ROWS:
                        payout = calculate_win_amount(int(game.bet_amount), FROG_MULTIPLIERS[-1])
                        user.dollar = int(user.dollar or 0) + payout
                        game.status = "won"
                        game.current_multiplier = FROG_MULTIPLIERS[-1]
                        game.win_amount = payout
                        session.add(_history(game, "won"))
                        _record_dollar(session, user, payout, "frog_win", f"Qurbaqa Yo'li maksimal yutuq #{game.id}", int(game.chat_id))
                        logger.info("frog_won user=%s session=%s payout=%s", tg_user_id, session_id, payout)
                        return FrogView(
                            "━━━━━━━━━━━━━━━━━━\n"
                            "🎉 <b>G'ALABA!</b>\n"
                            "━━━━━━━━━━━━━━━━━━\n\n"
                            "🐸 Qurbaqa manzilga yetib bordi!\n\n"
                            f"💰 Yutuq: <b>{payout}</b> ⭐\n"
                            f"📈 Yakuniy multiplikator: <b>x{FROG_MULTIPLIERS[-1]:.2f}</b>\n\n"
                            "✨ Bonus hisobingizga tushirildi.\n\n"
                            "━━━━━━━━━━━━━━━━━━",
                            build_frog_keyboard(game),
                            "🏆 Maksimal yutuq!",
                            True,
                        )

                    text = render_frog_text(
                        game,
                        int(user.dollar or 0),
                        "🐸 <b>Sakrash muvaffaqiyatli!</b>\n\nKeyingi platformani tanlang.",
                    )
                    return FrogView(text, build_frog_keyboard(game), "✅ Safe!")

    async def handle_frog_cashout(self, tg_user_id: int, session_id: int) -> FrogView:
        lock = _lock(int(session_id))
        async with lock:
            async with self.session_factory() as session:
                async with session.begin():
                    game = await self._session_for_update(session, session_id)
                    if game is None:
                        return FrogView("", None, "O'yin topilmadi.", True)
                    user = await session.get(User, int(game.user_id))
                    if user is None:
                        return FrogView("", None, "User topilmadi.", True)
                    guard = self._guard(game, user, tg_user_id)
                    if guard is not None:
                        return guard
                    if int(game.current_row or 0) <= 0:
                        return FrogView("", build_frog_keyboard(game), "❌ Avval kamida bitta qadam yuring.", True)

                    payout = calculate_win_amount(int(game.bet_amount), float(game.current_multiplier or 1.0))
                    user.dollar = int(user.dollar or 0) + payout
                    game.status = "cashed_out"
                    game.win_amount = payout
                    game.updated_at = _utcnow()
                    session.add(_history(game, "cashed_out"))
                    _record_dollar(session, user, payout, "frog_cashout", f"Qurbaqa Yo'li cashout #{game.id}", int(game.chat_id))
                    logger.info("frog_cashout user=%s session=%s payout=%s", tg_user_id, session_id, payout)
                    return FrogView(
                        "━━━━━━━━━━━━━━━━━━\n"
                        "💰 <b>YUTUQ OLINDI</b>\n"
                        "━━━━━━━━━━━━━━━━━━\n\n"
                        f"💰 Yutuq: <b>{payout}</b> ⭐\n"
                        f"📈 Multiplikator: <b>x{float(game.current_multiplier or 1.0):.2f}</b>\n"
                        f"💎 Balans: <b>{int(user.dollar or 0)}</b> ⭐\n\n"
                        "━━━━━━━━━━━━━━━━━━",
                        build_frog_keyboard(game),
                        f"💰 {payout} coin olindi!",
                        True,
                    )

    async def handle_frog_cancel(self, tg_user_id: int, session_id: int) -> FrogView:
        lock = _lock(int(session_id))
        async with lock:
            async with self.session_factory() as session:
                async with session.begin():
                    game = await self._session_for_update(session, session_id)
                    if game is None:
                        return FrogView("", None, "O'yin topilmadi.", True)
                    user = await session.get(User, int(game.user_id))
                    if user is None:
                        return FrogView("", None, "User topilmadi.", True)
                    guard = self._guard(game, user, tg_user_id)
                    if guard is not None:
                        return guard
                    game.status = "cancelled"
                    game.win_amount = 0
                    game.updated_at = _utcnow()
                    session.add(_history(game, "cancelled"))
                    logger.info("frog_cancelled user=%s session=%s", tg_user_id, session_id)
                    return FrogView(
                        "━━━━━━━━━━━━━━━━━━\n"
                        "❌ <b>O'YIN YAKUNLANDI</b>\n"
                        "━━━━━━━━━━━━━━━━━━\n\n"
                        f"💰 Stavka: <b>{int(game.bet_amount)}</b> ⭐\n"
                        "💸 Stavka qaytarilmaydi.\n\n"
                        "━━━━━━━━━━━━━━━━━━",
                        build_frog_keyboard(game),
                        "O'yin bekor qilindi.",
                        True,
                    )

    async def start_frog_game_message(self, message: Message, bet_amount: int) -> FrogView:
        if message.from_user is None:
            return FrogView("❌ User topilmadi.", None, "User topilmadi.", True)
        view = await self.start_frog_game(message.from_user, message.chat.id, bet_amount)
        sent = await message.answer(view.text, reply_markup=view.keyboard)
        if view.session_id:
            await self.set_message_id(view.session_id, sent.message_id)
        return view

    async def _get_or_create_user(self, session: AsyncSession, tg_user: TelegramUser) -> User:
        user = await session.scalar(select(User).where(User.telegram_id == int(tg_user.id)).with_for_update())
        display_name = (getattr(tg_user, "full_name", None) or getattr(tg_user, "first_name", None) or "User")[:255]
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

    async def _active_session(self, session: AsyncSession, user_id: int) -> Optional[FrogGameSession]:
        return await session.scalar(
            select(FrogGameSession)
            .where(FrogGameSession.user_id == int(user_id), FrogGameSession.status == FROG_ACTIVE)
            .order_by(FrogGameSession.id.desc())
            .with_for_update()
        )

    async def _session_for_update(self, session: AsyncSession, session_id: int) -> Optional[FrogGameSession]:
        return await session.scalar(
            select(FrogGameSession).where(FrogGameSession.id == int(session_id)).with_for_update()
        )

    def _guard(self, game: FrogGameSession, user: User, tg_user_id: int) -> Optional[FrogView]:
        if int(user.telegram_id) != int(tg_user_id):
            return FrogView("", None, "❌ Bu o'yin sizniki emas.", True)
        if game.status != FROG_ACTIVE:
            return FrogView("", None, "❌ Bu o'yin yakunlangan.", True)
        if game.status not in FROG_STATUSES:
            return FrogView("", None, "❌ O'yin holati noto'g'ri.", True)
        return None
