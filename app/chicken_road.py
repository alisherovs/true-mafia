"""
Chicken Road — premium risk ladder.

House-edge design:
- Step 1 can be danger (not free multiplier).
- Early multipliers stay low (x1.05 after first safe step).
- Death chance rises with progress.
- Difficulty changes mine count + death curve.
"""

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
CHICKEN_BET_OPTIONS = (100, 500, 1000, 5000, 10_000)
CHICKEN_GAME_TYPE = "chicken_road"
CHICKEN_ACTIVE = "active"
CHICKEN_STATUSES = {"active", "won", "lost", "cashed_out", "cancelled"}
CHICKEN_SEP = "━━━━━━━━━━━━━━━"
CHICKEN_MONEY_EMOJI_ID = "5409048419211682843"
CHICKEN_MINE_EMOJI_ID = "5469654973308476699"

# Cashout multiplier AFTER surviving step N (step 0 = not started → no cashout)
# Step 1 is intentionally tiny so free rides are worthless.
CHICKEN_MULTIPLIERS: dict[int, float] = {
    1: 1.05,
    2: 1.18,
    3: 1.38,
    4: 1.70,
    5: 2.20,
    6: 3.00,
    7: 4.40,
    8: 7.00,
}

# Base death probability when stepping onto cell N (before difficulty scale)
# Step 1 is dangerous on purpose (~30% base death on normal).
CHICKEN_BASE_DEATH: dict[int, float] = {
    1: 0.30,
    2: 0.22,
    3: 0.20,
    4: 0.22,
    5: 0.26,
    6: 0.30,
    7: 0.34,
    8: 0.40,
}

DIFFICULTY_DEATH_SCALE = {
    "easy": 0.85,  # slightly safer
    "normal": 1.00,
    "hard": 1.22,  # harsher
}

DIFFICULTY_LABELS = {
    "easy": "🟢 Oson",
    "normal": "🟡 Oddiy",
    "hard": "🔴 Qiyin",
}

# Soft cap so death never hits 100%
MAX_DEATH_CHANCE = 0.72
MIN_DEATH_CHANCE = 0.12

CHICKEN_LOCKS: dict[int, asyncio.Lock] = {}
CHICKEN_USER_LOCKS: dict[int, asyncio.Lock] = {}
_DIFF_PREF: dict[int, str] = {}  # telegram_id -> difficulty (shared across engine instances)
_RNG = secrets.SystemRandom()


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


def _money() -> str:
    return _ce("💵", CHICKEN_MONEY_EMOJI_ID)


def _btn(text: str, callback_data: str, style: str = "primary") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data, **{"style": style})


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


def normalize_difficulty(raw: str | None) -> str:
    d = (raw or "normal").lower().strip()
    if d in {"easy", "oson"}:
        return "easy"
    if d in {"hard", "qiyin"}:
        return "hard"
    return "normal"


def death_chance(step: int, difficulty: str = "normal") -> float:
    """Probability of dying when attempting to enter this step."""
    base = CHICKEN_BASE_DEATH.get(int(step), 0.30)
    scale = DIFFICULTY_DEATH_SCALE.get(normalize_difficulty(difficulty), 1.0)
    return max(MIN_DEATH_CHANCE, min(MAX_DEATH_CHANCE, base * scale))


def roll_danger_for_step(step: int, difficulty: str) -> bool:
    """True = mine / death on this step."""
    p = death_chance(step, difficulty)
    return _RNG.random() < p


def generate_road_map(difficulty: str = "normal") -> list[str]:
    """
    Pre-generate 8 cells with independent death rolls.
    Step 1 CAN be danger — no free first multiplier.
    """
    difficulty = normalize_difficulty(difficulty)
    road: list[str] = []
    for step in range(1, CHICKEN_STEPS + 1):
        road.append("danger" if roll_danger_for_step(step, difficulty) else "safe")
    # Ensure at least one danger exists (house edge floor)
    if "danger" not in road:
        road[_RNG.randrange(0, CHICKEN_STEPS)] = "danger"
    # Ensure at least one safe path is not guaranteed to max — already probabilistic
    return road


def calculate_chicken_multiplier(step: int) -> float:
    if step <= 0:
        return 1.0
    return float(CHICKEN_MULTIPLIERS.get(min(int(step), CHICKEN_STEPS), 1.0))


def calculate_chicken_win(bet_amount: int, multiplier: float) -> int:
    return max(0, int(int(bet_amount) * float(multiplier)))


def next_step_info(current_step: int, difficulty: str) -> tuple[int, float, float]:
    """Returns (next_step, next_mult, death_pct)."""
    nxt = int(current_step) + 1
    if nxt > CHICKEN_STEPS:
        return CHICKEN_STEPS, calculate_chicken_multiplier(CHICKEN_STEPS), 0.0
    return nxt, calculate_chicken_multiplier(nxt), death_chance(nxt, difficulty) * 100


def parse_chicken_callback(data: str) -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    """
    Returns (action, value/session_id, owner_id, extra).
    Formats:
      chicken:menu:{owner}
      chicken:diff:{owner}:{easy|normal|hard}
      chicken:bet:{owner}:{amount}
      chicken:bet:{amount}  (legacy)
      chicken:custom_bet:{owner}
      chicken:back:{owner}
      chicken:go:{session}
      chicken:cashout:{session}
      chicken:cancel:{session}
      chicken:noop:{session}
      chicken:again:{owner}
    """
    parts = (data or "").split(":")
    if len(parts) < 2:
        raise ValueError("bad_callback")
    if parts[0] == "qimor" and parts[1] == "chicken":
        return "menu", None, None, None
    if parts[0] != "chicken":
        raise ValueError("bad_callback")

    action = parts[1]

    if action in {"menu", "again", "custom_bet", "back"}:
        owner = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else None
        return action, None, owner, None

    if action == "diff" and len(parts) >= 4 and parts[2].isdigit() and parts[3] in {"easy", "normal", "hard"}:
        return action, None, int(parts[2]), parts[3]

    if action == "bet":
        if len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
            return action, int(parts[3]), int(parts[2]), None
        if len(parts) == 3 and parts[2].isdigit():
            return action, int(parts[2]), None, None
        raise ValueError("bad_callback")

    if action in {"go", "cashout", "cancel", "noop"} and len(parts) >= 3 and parts[2].isdigit():
        return action, int(parts[2]), None, None

    raise ValueError("bad_callback")


def chicken_start_text(balance: int | None = None, difficulty: str = "normal") -> str:
    d = normalize_difficulty(difficulty)
    lines = [
        CHICKEN_SEP,
        "🐔 <b>CHICKEN ROAD</b>",
        CHICKEN_SEP,
        "",
        "💎 Premium risk ladder",
        "8 qadam · xavf oshib boradi",
        "1-qadam <b>ham</b> xavfli bo‘lishi mumkin",
        "",
        f"🎚 Daraja: <b>{DIFFICULTY_LABELS[d]}</b>",
        f"📉 1-qadam: x{CHICKEN_MULTIPLIERS[1]:.2f} · ☠️ ~{death_chance(1, d)*100:.0f}%",
        f"📈 Final: x{CHICKEN_MULTIPLIERS[8]:.2f} · ☠️ ~{death_chance(8, d)*100:.0f}%",
        "",
        f"Min <b>{CHICKEN_MIN_BET}</b> · Max <b>{CHICKEN_MAX_BET}</b>",
    ]
    if balance is not None:
        lines.append(f"{_money()} Balans: <b>{int(balance)}</b>")
    lines.extend(["", "💰 <b>Stavkani tanlang</b>"])
    return "\n".join(lines)


def build_chicken_start_keyboard(
    owner_id: int | None = None,
    difficulty: str = "normal",
) -> InlineKeyboardMarkup:
    o = int(owner_id) if owner_id is not None else 0
    d = normalize_difficulty(difficulty)

    def bet_cb(amount: int) -> str:
        return f"chicken:bet:{o}:{amount}" if owner_id is not None else f"chicken:bet:{amount}"

    diff_row = [
        _btn(
            ("✅ " if d == key else "") + label.split(" ", 1)[1],
            f"chicken:diff:{o}:{key}",
            "success" if d == key else "primary",
        )
        for key, label in (("easy", "🟢 Oson"), ("normal", "🟡 Oddiy"), ("hard", "🔴 Qiyin"))
    ]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            diff_row,
            [
                _btn("100", bet_cb(100), "primary"),
                _btn("500", bet_cb(500), "primary"),
                _btn("1000", bet_cb(1000), "primary"),
            ],
            [
                _btn("5000", bet_cb(5000), "primary"),
                _btn("10000", bet_cb(10000), "primary"),
            ],
            [_btn("✍️ Boshqa summa", f"chicken:custom_bet:{o}", "success")],
            [_btn("⬅️ Ortga", f"chicken:back:{o}", "danger")],
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
        _btn(_chicken_cell_text(session, step), f"chicken:noop:{session.id}", "primary")
        for step in range(CHICKEN_STEPS, 0, -1)
    ]


def _build_chicken_keyboard_for_session(session: ChickenRoadSession) -> InlineKeyboardMarkup:
    step = int(session.current_step or 0)
    can_cash = step > 0
    rows: list[list[InlineKeyboardButton]] = [
        _chicken_road_buttons(session),
        [_btn("🚶 Oldinga yurish", f"chicken:go:{session.id}", "success")],
    ]
    if can_cash:
        win = calculate_chicken_win(int(session.bet_amount), float(session.current_multiplier or 1.0))
        rows.append([_btn(f"💰 Pulni olish · {win}", f"chicken:cashout:{session.id}", "primary")])
    else:
        rows.append([_btn("💰 Pulni olish (avval yuring)", f"chicken:noop:{session.id}", "primary")])
    rows.append([_btn("❌ Taslim", f"chicken:cancel:{session.id}", "danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    diff = normalize_difficulty(getattr(session, "difficulty", None) or "normal")
    nxt, nxt_mult, death_pct = next_step_info(current_step, diff)

    parts = [
        CHICKEN_SEP,
        "🐔 <b>CHICKEN ROAD</b>",
        CHICKEN_SEP,
        "",
        render_chicken_board(session),
        "",
        f"{_money()} Stavka: <b>{int(session.bet_amount)}</b>",
        f"🎚 Daraja: <b>{DIFFICULTY_LABELS.get(diff, diff)}</b>",
        f"🚶 Qadam: <b>{current_step}</b>/<b>{CHICKEN_STEPS}</b>",
        f"📈 Multi: <b>x{multiplier:.2f}</b>",
        f"🏆 Olish: <b>{current_win}</b>",
        f"💼 Balans: <b>{int(user_balance)}</b>",
    ]
    if session.status == CHICKEN_ACTIVE and current_step < CHICKEN_STEPS:
        parts.extend(
            [
                "",
                f"⏭ Keyingi: <b>x{nxt_mult:.2f}</b> · ☠️ ~<b>{death_pct:.0f}%</b>",
            ]
        )
        if current_step == 0:
            parts.append("⚠️ <i>1-qadam bepul emas — yiqilishingiz mumkin</i>")
    if result:
        parts.extend(["", result])
    elif session.status == CHICKEN_ACTIVE:
        parts.extend(["", "Davom etasizmi yoki pulni olasizmi?"])
    return "\n".join(parts)


def end_text(
    *,
    title: str,
    bet: int,
    step: int,
    mult: float,
    payout: int,
    board: str,
    extra: str = "",
) -> str:
    lines = [
        CHICKEN_SEP,
        f"🐔 <b>{title}</b>",
        CHICKEN_SEP,
        "",
        board,
        "",
        f"{_money()} Stavka: <b>{int(bet)}</b>",
        f"🚶 Qadam: <b>{int(step)}</b>/{CHICKEN_STEPS}",
        f"📈 Multi: <b>x{float(mult):.2f}</b>",
    ]
    if payout > 0:
        lines.append(f"🏆 Yutuq: <b>{int(payout)}</b>")
    else:
        lines.append("💸 Stavka kuyib ketdi")
    if extra:
        lines.extend(["", extra])
    lines.append(CHICKEN_SEP)
    return "\n".join(lines)


def build_end_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    o = int(owner_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🔁 Yana o‘ynash", f"chicken:again:{o}", "success")],
            [_btn("⬅️ Menyuga", f"chicken:back:{o}", "danger")],
        ]
    )


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

    def set_difficulty_pref(self, telegram_id: int, difficulty: str) -> None:
        _DIFF_PREF[int(telegram_id)] = normalize_difficulty(difficulty)

    def get_difficulty_pref(self, telegram_id: int) -> str:
        return _DIFF_PREF.get(int(telegram_id), "normal")

    async def get_balance(self, telegram_id: int) -> int:
        async with self.session_factory() as session:
            bal = await session.scalar(select(User.dollar).where(User.telegram_id == int(telegram_id)))
            return int(bal or 0)

    async def home(self, telegram_id: int, owner_id: int | None = None) -> ChickenView:
        bal = await self.get_balance(telegram_id)
        oid = int(owner_id or telegram_id)
        diff = self.get_difficulty_pref(telegram_id)
        return ChickenView(
            chicken_start_text(bal, diff),
            build_chicken_start_keyboard(oid, diff),
        )

    async def start_chicken_game(
        self,
        tg_user: TelegramUser,
        chat_id: int,
        bet_amount: int,
        difficulty: str | None = None,
    ) -> ChickenView:
        bet_amount = int(bet_amount)
        diff = normalize_difficulty(difficulty or self.get_difficulty_pref(int(tg_user.id)))
        owner_kb = build_chicken_start_keyboard(int(tg_user.id), diff)

        if bet_amount < CHICKEN_MIN_BET or bet_amount > CHICKEN_MAX_BET:
            return ChickenView(
                f"❌ Stavka <b>{CHICKEN_MIN_BET}</b> – <b>{CHICKEN_MAX_BET}</b> oralig‘ida.",
                owner_kb,
                "Stavka noto‘g‘ri.",
                True,
            )

        async with _user_lock(int(tg_user.id)):
            async with self.session_factory() as session:
                async with session.begin():
                    user = await self._get_or_create_user(session, tg_user)
                    active = await self._active_session(session, int(user.id))
                    if active is not None:
                        text = render_chicken_text(
                            active,
                            int(user.dollar or 0),
                            "Davom etayotgan o‘yiningiz tiklandi.",
                        )
                        return ChickenView(
                            text,
                            _build_chicken_keyboard_for_session(active),
                            "Sizda aktiv o‘yin bor.",
                            True,
                            int(active.id),
                        )
                    if int(user.dollar or 0) < bet_amount:
                        return ChickenView(
                            f"❌ Balans yetarli emas.\n{_money()} Balans: <b>{int(user.dollar or 0)}</b>",
                            owner_kb,
                            "Balans yetarli emas.",
                            True,
                        )

                    user.dollar = max(0, int(user.dollar or 0) - bet_amount)
                    road = generate_road_map(diff)
                    game = ChickenRoadSession(
                        user_id=int(user.id),
                        chat_id=int(chat_id),
                        bet_amount=bet_amount,
                        current_step=0,
                        current_multiplier=1.0,
                        difficulty=diff,
                        road_map=json.dumps(road, ensure_ascii=False),
                        status=CHICKEN_ACTIVE,
                        win_amount=0,
                    )
                    session.add(game)
                    await session.flush()
                    _record_dollar(
                        session,
                        user,
                        -bet_amount,
                        "chicken_bet",
                        f"Chicken Road stavka #{game.id} {diff}",
                        chat_id,
                    )
                    logger.info(
                        "chicken_started user=%s session=%s bet=%s diff=%s road=%s",
                        tg_user.id,
                        game.id,
                        bet_amount,
                        diff,
                        road,
                    )
                    return ChickenView(
                        render_chicken_text(game, int(user.dollar or 0)),
                        _build_chicken_keyboard_for_session(game),
                        session_id=int(game.id),
                    )

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
                        return ChickenView("", None, "❌ O‘yin topilmadi.", True)
                    user = await session.get(User, int(game.user_id))
                    if user is None:
                        return ChickenView("", None, "User topilmadi.", True)
                    guard = self._guard(game, user, tg_user_id)
                    if guard is not None:
                        return guard

                    next_step = int(game.current_step or 0) + 1
                    if next_step > CHICKEN_STEPS:
                        return ChickenView("", None, "❌ Bu o‘yin yakunlangan.", True)

                    road_map = _json_loads(game.road_map, [])
                    # Safety: regenerate incomplete maps
                    if len(road_map) < CHICKEN_STEPS:
                        road_map = generate_road_map(game.difficulty or "normal")
                        game.road_map = json.dumps(road_map, ensure_ascii=False)

                    danger = (next_step - 1) < len(road_map) and road_map[next_step - 1] == "danger"
                    game.current_step = next_step
                    game.current_multiplier = calculate_chicken_multiplier(next_step)
                    game.updated_at = _utcnow()
                    owner_id = int(user.telegram_id)

                    if danger:
                        game.status = "lost"
                        game.win_amount = 0
                        session.add(_history(game, "lost"))
                        logger.info(
                            "chicken_lost user=%s session=%s step=%s",
                            tg_user_id,
                            session_id,
                            next_step,
                        )
                        board = render_chicken_board(game, reveal_danger=True)
                        return ChickenView(
                            end_text(
                                title="YIQILDINGIZ",
                                bet=int(game.bet_amount),
                                step=next_step,
                                mult=float(game.current_multiplier or 1.0),
                                payout=0,
                                board=board,
                                extra=f"{_ce('💣', CHICKEN_MINE_EMOJI_ID)} Tovuq yo‘lda urildi!",
                            ),
                            build_end_keyboard(owner_id),
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
                        _record_dollar(
                            session,
                            user,
                            payout,
                            "chicken_win",
                            f"Chicken Road max #{game.id}",
                            int(game.chat_id),
                        )
                        logger.info(
                            "chicken_won user=%s session=%s payout=%s",
                            tg_user_id,
                            session_id,
                            payout,
                        )
                        return ChickenView(
                            end_text(
                                title="FINISH!",
                                bet=int(game.bet_amount),
                                step=next_step,
                                mult=CHICKEN_MULTIPLIERS[CHICKEN_STEPS],
                                payout=payout,
                                board=render_chicken_board(game),
                                extra="🏆 Barcha qadamlarni o‘tdingiz!",
                            ),
                            build_end_keyboard(owner_id),
                            "🏆 Maksimal yutuq!",
                            True,
                        )

                    note = "✅ Xavfsiz qadam!"
                    if next_step == 1:
                        note = f"✅ 1-qadam o‘tdi · x{game.current_multiplier:.2f} (kichik start)"
                    return ChickenView(
                        render_chicken_text(game, int(user.dollar or 0), note),
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
                        return ChickenView("", None, "❌ O‘yin topilmadi.", True)
                    user = await session.get(User, int(game.user_id))
                    if user is None:
                        return ChickenView("", None, "User topilmadi.", True)
                    guard = self._guard(game, user, tg_user_id)
                    if guard is not None:
                        return guard
                    if int(game.current_step or 0) <= 0:
                        return ChickenView(
                            "",
                            _build_chicken_keyboard_for_session(game),
                            "❌ Avval kamida 1 qadam yuring.",
                            True,
                        )

                    payout = calculate_chicken_win(
                        int(game.bet_amount),
                        float(game.current_multiplier or 1.0),
                    )
                    user.dollar = int(user.dollar or 0) + payout
                    game.status = "cashed_out"
                    game.win_amount = payout
                    game.updated_at = _utcnow()
                    session.add(_history(game, "cashed_out"))
                    _record_dollar(
                        session,
                        user,
                        payout,
                        "chicken_cashout",
                        f"Chicken Road cashout #{game.id}",
                        int(game.chat_id),
                    )
                    logger.info(
                        "chicken_cashout user=%s session=%s payout=%s",
                        tg_user_id,
                        session_id,
                        payout,
                    )
                    return ChickenView(
                        end_text(
                            title="CASHOUT",
                            bet=int(game.bet_amount),
                            step=int(game.current_step or 0),
                            mult=float(game.current_multiplier or 1.0),
                            payout=payout,
                            board=render_chicken_board(game),
                            extra="💰 Pul muvaffaqiyatli olindi.",
                        ),
                        build_end_keyboard(int(user.telegram_id)),
                        f"💰 {payout} olindi!",
                        True,
                    )

    async def handle_chicken_cancel(self, tg_user_id: int, session_id: int) -> ChickenView:
        lock = _lock(int(session_id))
        async with lock:
            async with self.session_factory() as session:
                async with session.begin():
                    game = await self._session_for_update(session, session_id)
                    if game is None:
                        return ChickenView("", None, "❌ O‘yin topilmadi.", True)
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
                        end_text(
                            title="TASLIM",
                            bet=int(game.bet_amount),
                            step=int(game.current_step or 0),
                            mult=float(game.current_multiplier or 1.0),
                            payout=0,
                            board=render_chicken_board(game, reveal_danger=True),
                            extra="💸 Stavka qaytarilmaydi.",
                        ),
                        build_end_keyboard(int(user.telegram_id)),
                        "O‘yin bekor qilindi.",
                        True,
                    )

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

    async def _active_session(self, session: AsyncSession, user_id: int) -> Optional[ChickenRoadSession]:
        return await session.scalar(
            select(ChickenRoadSession)
            .where(
                ChickenRoadSession.user_id == int(user_id),
                ChickenRoadSession.status == CHICKEN_ACTIVE,
            )
            .order_by(ChickenRoadSession.id.desc())
            .with_for_update()
        )

    async def _session_for_update(self, session: AsyncSession, session_id: int) -> Optional[ChickenRoadSession]:
        return await session.scalar(
            select(ChickenRoadSession)
            .where(ChickenRoadSession.id == int(session_id))
            .with_for_update()
        )

    def _guard(self, game: ChickenRoadSession, user: User, tg_user_id: int) -> Optional[ChickenView]:
        if int(user.telegram_id) != int(tg_user_id):
            return ChickenView("", None, "❌ Bu o‘yin sizniki emas.", True)
        if game.status != CHICKEN_ACTIVE:
            return ChickenView("", None, "❌ Bu o‘yin yakunlangan.", True)
        if game.status not in CHICKEN_STATUSES:
            return ChickenView("", None, "❌ O‘yin holati noto‘g‘ri.", True)
        return None

