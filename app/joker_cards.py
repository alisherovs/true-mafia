"""
Joker Cards — premium pick-a-card mini-game.

Difficulties expose win odds and multipliers before play.
Outcome uses CSPRNG (secrets.SystemRandom). Integrates with
User.dollar, DollarTransaction, GameHistory.
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

from app.models import DollarTransaction, GameHistory, JokerCardsGame, JokerCardsUserStats, User

logger = logging.getLogger(__name__)

JOKER_GAME_TYPE = "joker_cards"
JOKER_MIN_BET = 100
JOKER_MAX_BET = 100_000
JOKER_SEP = "━━━━━━━━━━━━━━━"
JOKER_MONEY_EMOJI_ID = "5409048419211682843"
_RNG = secrets.SystemRandom()

_USER_LOCKS: dict[int, asyncio.Lock] = {}
_IN_FLIGHT: set[int] = set()
_DIFF_PREF: dict[int, str] = {}

# Deck layout: list of outcomes as multipliers (0.0 = loss)
# Win odds = count(m>0) / len(deck)
DIFFICULTIES: dict[str, dict] = {
    "easy": {
        "label": "🟢 Easy",
        "title": "Easy",
        "deck": [1.2, 1.8, 0.0],
        "blurb": "3 karta · 2 yutuq · 1 mag‘lubiyat",
    },
    "normal": {
        "label": "🟡 Normal",
        "title": "Normal",
        "deck": [1.5, 2.0, 0.0, 0.0],
        "blurb": "4 karta · 2 yutuq · 2 mag‘lubiyat",
    },
    "hard": {
        "label": "🔴 Hard",
        "title": "Hard",
        "deck": [5.0, 0.0, 0.0, 0.0, 0.0],
        "blurb": "5 karta · 1 yutuq · 4 mag‘lubiyat",
    },
    "nightmare": {
        "label": "☠️ Nightmare",
        "title": "Nightmare",
        "deck": [10.0, 15.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # display only; runtime = 1 win + 8 loss
        "blurb": "9 karta · 1 yutuq (x10 yoki x15) · 8 mag‘lubiyat",
    },
}
# Note: Nightmare user said 1 win x10 or x15 and 8 losses = 9 cards.
# "1 ta yutuq (x10 yoki x15)" could mean one win card that is either 10 or 15,
# OR one of two win cards. Using two distinct win cards among 9 (2 win / 7 loss)
# would be ~22% win. User said 11.1%/88.9% = 1/9 win.
# So exactly 1 win card: randomly fixed to either x10 or x15 at deck build,
# OR the single win slot is chosen as x10 or x15 when drawing.
# Best: deck has 8 losses + 1 win, and that win multiplier is secrets.choice([10,15])
# at game start when building deck for nightmare.

DIFFICULTY_ORDER = ("easy", "normal", "hard", "nightmare")


@dataclass(frozen=True)
class JokerView:
    text: str
    keyboard: Optional[InlineKeyboardMarkup] = None
    alert: str = ""
    show_alert: bool = False
    played: bool = False
    won: bool = False
    bet: int = 0
    difficulty: str = ""
    multiplier: float = 0.0
    profit: int = 0
    balance: int = 0
    game_id: Optional[int] = None
    pick_index: int = -1
    deck_size: int = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ce(symbol: str, emoji_id: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{symbol}</tg-emoji>'


def _money() -> str:
    return _ce("💵", JOKER_MONEY_EMOJI_ID)


def _btn(text: str, callback_data: str, style: str = "primary") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data, **{"style": style})


def _user_lock(telegram_id: int) -> asyncio.Lock:
    lock = _USER_LOCKS.get(int(telegram_id))
    if lock is None:
        lock = asyncio.Lock()
        _USER_LOCKS[int(telegram_id)] = lock
    return lock


def normalize_diff(raw: str | None) -> str:
    d = (raw or "normal").lower().strip()
    return d if d in DIFFICULTIES else "normal"


def build_deck(difficulty: str) -> list[float]:
    """Build shuffled deck with CSPRNG. Nightmare: exactly 1 win card (x10 or x15)."""
    d = normalize_diff(difficulty)
    if d == "nightmare":
        win_mult = float(_RNG.choice([10.0, 15.0]))
        deck = [win_mult] + [0.0] * 8
    else:
        deck = [float(x) for x in DIFFICULTIES[d]["deck"]]
    _RNG.shuffle(deck)
    return deck


def deck_stats(difficulty: str) -> dict:
    """Public odds for UI (before shuffle identity)."""
    d = normalize_diff(difficulty)
    if d == "nightmare":
        # 1 win of 9; win is either x10 or x15 (equal prior for display)
        total = 9
        wins = 1
        losses = 8
        mults = [10.0, 15.0]
        win_pct = 100.0 * wins / total
        loss_pct = 100.0 * losses / total
        # Expected multiplier if win path: avg 12.5
        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_pct": win_pct,
            "loss_pct": loss_pct,
            "mults": mults,
            "label": DIFFICULTIES[d]["label"],
            "blurb": DIFFICULTIES[d]["blurb"],
            # EV ≈ (1/9)*12.5 ≈ 1.39 on win side of stake return... payout mult
            "ev_mult": (1 / 9) * 12.5,
        }
    deck = [float(x) for x in DIFFICULTIES[d]["deck"]]
    total = len(deck)
    wins = sum(1 for x in deck if x > 0)
    losses = total - wins
    mults = sorted({x for x in deck if x > 0})
    win_pct = 100.0 * wins / total if total else 0.0
    loss_pct = 100.0 * losses / total if total else 0.0
    ev = sum(deck) / total if total else 0.0
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_pct": win_pct,
        "loss_pct": loss_pct,
        "mults": mults,
        "label": DIFFICULTIES[d]["label"],
        "blurb": DIFFICULTIES[d]["blurb"],
        "ev_mult": ev,
    }


def validate_bet(amount: int, balance: int) -> tuple[bool, str]:
    if amount <= 0:
        return False, "Stavka noto‘g‘ri."
    if amount < JOKER_MIN_BET:
        return False, f"Min stavka: <b>{JOKER_MIN_BET}</b> {_money()}"
    if amount > JOKER_MAX_BET:
        return False, f"Max stavka: <b>{JOKER_MAX_BET}</b> {_money()}"
    if amount > balance:
        return False, f"Balans yetarli emas.\nBalans: <b>{balance}</b> {_money()}"
    return True, ""


def parse_joker_callback(data: str) -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    """
    jk:menu:{owner}
    jk:diff:{owner}:{easy|normal|hard|nightmare}
    jk:bet:{owner}:{amount}
    jk:half:{owner}
    jk:all:{owner}
    jk:min:{owner}
    jk:max:{owner}
    jk:custom:{owner}
    jk:pick:{owner}:{amount}:{diff}:{index}
    jk:confirm:{owner}:{amount}:{diff}
    jk:again:{owner}
    jk:stats:{owner}
    jk:hist:{owner}
    jk:back:{owner}
    """
    parts = (data or "").split(":")
    if len(parts) < 2 or parts[0] != "jk":
        raise ValueError("bad_callback")
    action = parts[1]

    if action in {"menu", "again", "stats", "hist", "back", "half", "all", "min", "max", "custom"}:
        if len(parts) < 3 or not parts[2].isdigit():
            raise ValueError("bad_callback")
        return action, int(parts[2]), None, None

    if action == "diff" and len(parts) >= 4 and parts[2].isdigit() and parts[3] in DIFFICULTIES:
        return action, int(parts[2]), None, parts[3]

    if action == "bet" and len(parts) >= 4 and parts[2].isdigit() and parts[3].isdigit():
        return action, int(parts[2]), int(parts[3]), None

    if action == "confirm" and len(parts) >= 5 and parts[2].isdigit() and parts[3].isdigit() and parts[4] in DIFFICULTIES:
        return action, int(parts[2]), int(parts[3]), parts[4]

    if (
        action == "pick"
        and len(parts) >= 6
        and parts[2].isdigit()
        and parts[3].isdigit()
        and parts[4] in DIFFICULTIES
        and parts[5].isdigit()
    ):
        return action, int(parts[2]), int(parts[3]), f"{parts[4]}:{parts[5]}"

    raise ValueError("bad_callback")


def odds_block(difficulty: str) -> str:
    s = deck_stats(difficulty)
    mults = " · ".join(f"x{m:g}" for m in s["mults"])
    return (
        f"{s['label']}\n"
        f"├ {s['blurb']}\n"
        f"├ ✅ Yutuq: <b>{s['win_pct']:.1f}%</b> ({s['wins']}/{s['total']})\n"
        f"├ ❌ Mag‘lubiyat: <b>{s['loss_pct']:.1f}%</b> ({s['losses']}/{s['total']})\n"
        f"└ 🎯 Multi: <b>{mults}</b>"
    )


def home_text(balance: int, difficulty: str) -> str:
    d = normalize_diff(difficulty)
    return (
        f"{JOKER_SEP}\n"
        f"🃏 <b>JOKER CARDS</b>\n"
        f"{JOKER_SEP}\n\n"
        f"💎 Premium karta o‘yini\n"
        f"Kartani tanlang — yutuq yoki joker (0).\n\n"
        f"{odds_block(d)}\n\n"
        f"{_money()} Balans: <b>{int(balance)}</b>\n"
        f"📉 Min: <b>{JOKER_MIN_BET}</b> · 📈 Max: <b>{JOKER_MAX_BET}</b>\n\n"
        f"💰 <b>Stavka va darajani tanlang</b>"
    )


def build_home_keyboard(owner_id: int, balance: int, difficulty: str) -> InlineKeyboardMarkup:
    o = int(owner_id)
    d = normalize_diff(difficulty)
    max_shown = min(JOKER_MAX_BET, max(JOKER_MIN_BET, int(balance)))
    diff_row = [
        _btn(
            ("✅ " if d == key else "") + DIFFICULTIES[key]["title"][:3],
            f"jk:diff:{o}:{key}",
            "success" if d == key else "primary",
        )
        for key in DIFFICULTY_ORDER
    ]
    # shorter labels for row fit
    labels = {"easy": "Easy", "normal": "Norm", "hard": "Hard", "nightmare": "Night"}
    diff_row = [
        _btn(
            ("✅" if d == key else "") + labels[key],
            f"jk:diff:{o}:{key}",
            "success" if d == key else "primary",
        )
        for key in DIFFICULTY_ORDER
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            diff_row,
            [
                _btn("100", f"jk:bet:{o}:100", "primary"),
                _btn("500", f"jk:bet:{o}:500", "primary"),
                _btn("1000", f"jk:bet:{o}:1000", "primary"),
            ],
            [
                _btn("5000", f"jk:bet:{o}:5000", "primary"),
                _btn("10000", f"jk:bet:{o}:10000", "primary"),
            ],
            [
                _btn(f"Min {JOKER_MIN_BET}", f"jk:min:{o}", "success"),
                _btn(f"Max {max_shown}", f"jk:max:{o}", "success"),
            ],
            [
                _btn("½ Balans", f"jk:half:{o}", "primary"),
                _btn("🔥 All In", f"jk:all:{o}", "danger"),
            ],
            [_btn("✍️ Boshqa summa", f"jk:custom:{o}", "success")],
            [
                _btn("📊 Statistika", f"jk:stats:{o}", "primary"),
                _btn("📜 Tarix", f"jk:hist:{o}", "primary"),
            ],
            [_btn("⬅️ Ortga", f"jk:back:{o}", "danger")],
        ]
    )


def pick_text(balance: int, bet: int, difficulty: str) -> str:
    d = normalize_diff(difficulty)
    s = deck_stats(d)
    pot_max = int(bet * max(s["mults"]))
    return (
        f"{JOKER_SEP}\n"
        f"🃏 <b>KARTA TANLANG</b>\n"
        f"{JOKER_SEP}\n\n"
        f"{odds_block(d)}\n\n"
        f"{_money()} Stavka: <b>{int(bet)}</b>\n"
        f"💼 Balans: <b>{int(balance)}</b>\n"
        f"🏆 Maks potensial: <b>{pot_max}</b>\n\n"
        f"🔀 Kartalar aralashtirilgan (CSPRNG)\n"
        f"Qaysi kartani ochasiz?"
    )


def build_pick_keyboard(owner_id: int, bet: int, difficulty: str) -> InlineKeyboardMarkup:
    o = int(owner_id)
    d = normalize_diff(difficulty)
    n = deck_stats(d)["total"]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i in range(n):
        row.append(_btn(f"🂠 {i + 1}", f"jk:pick:{o}:{int(bet)}:{d}:{i}", "primary"))
        if len(row) == 3 or i == n - 1:
            rows.append(row)
            row = []
    rows.append([_btn("❌ Bekor", f"jk:menu:{o}", "danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def flipping_card_text(frame: int = 0) -> str:
    faces = ("🂠", "🃏", "✨", "🂡", "💫")
    dots = "·" * ((frame % 3) + 1)
    return (
        f"{JOKER_SEP}\n"
        f"🃏 <b>KARTA OCHILMOQDA</b>\n"
        f"{JOKER_SEP}\n\n"
        f"      {faces[frame % len(faces)]}  {faces[(frame + 1) % len(faces)]}  {faces[(frame + 2) % len(faces)]}\n\n"
        f"⏳ Kutib turing{dots}"
    )


def result_text(
    *,
    bet: int,
    difficulty: str,
    mult: float,
    won: bool,
    profit: int,
    balance: int,
    pick_index: int,
    deck_size: int,
    reveal_line: str = "",
) -> str:
    d = normalize_diff(difficulty)
    title = "🏆 YUTDINGIZ!" if won else "💀 JOKER — YUTQAZDINGIZ"
    profit_line = (
        f"📈 Sof foyda: <b>+{int(profit)}</b> {_money()}"
        if won
        else f"📉 Zarar: <b>{int(profit)}</b> {_money()}"
    )
    mult_line = f"🎯 Multi: <b>x{mult:g}</b>" if won else "🎯 Multi: <b>x0</b>"
    return (
        f"{JOKER_SEP}\n"
        f"🃏 <b>JOKER CARDS</b>\n"
        f"{JOKER_SEP}\n\n"
        f"<b>{title}</b>\n\n"
        f"🎚 Daraja: <b>{DIFFICULTIES[d]['label']}</b>\n"
        f"{_money()} Stavka: <b>{int(bet)}</b>\n"
        f"🃏 Tanlov: <b>#{int(pick_index) + 1}</b> / {int(deck_size)}\n"
        f"{mult_line}\n"
        f"{profit_line}\n"
        f"💼 Yangi balans: <b>{int(balance)}</b>\n"
        f"{reveal_line}"
        f"\n{JOKER_SEP}"
    )


def build_result_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    o = int(owner_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🔁 Yana o‘ynash", f"jk:again:{o}", "success")],
            [
                _btn("📊 Statistika", f"jk:stats:{o}", "primary"),
                _btn("📜 Tarix", f"jk:hist:{o}", "primary"),
            ],
            [_btn("⬅️ Ortga", f"jk:back:{o}", "danger")],
        ]
    )


def stats_text(stats: JokerCardsUserStats | None, balance: int) -> str:
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
        f"{JOKER_SEP}\n"
        f"📊 <b>JOKER CARDS STATISTIKA</b>\n"
        f"{JOKER_SEP}\n\n"
        f"🎮 O‘yinlar: <b>{played}</b>\n"
        f"✅ Yutuq: <b>{wins}</b>\n"
        f"❌ Mag‘lubiyat: <b>{losses}</b>\n"
        f"📈 Win rate: <b>{rate:.1f}%</b>\n"
        f"💰 Umumiy foyda: <b>{profit_s}</b>\n"
        f"🏅 Eng katta yutuq: <b>{biggest}</b>\n"
        f"🔥 Seriya: <b>{streak}</b>\n"
        f"🏆 Eng yaxshi seriya: <b>{best}</b>\n\n"
        f"{_money()} Balans: <b>{int(balance)}</b>\n"
        f"{JOKER_SEP}"
    )


def history_text(rows: list[JokerCardsGame]) -> str:
    if not rows:
        return (
            f"{JOKER_SEP}\n"
            f"📜 <b>JOKER TARIX</b>\n"
            f"{JOKER_SEP}\n\n"
            f"Hali o‘yin yo‘q.\n"
            f"Birinchi kartani oching 🃏"
        )
    lines = [f"{JOKER_SEP}\n📜 <b>OXIRGI O‘YINLAR</b>\n{JOKER_SEP}\n"]
    for g in rows[:12]:
        icon = "✅" if g.won else "💀"
        mult = f"x{float(g.multiplier):g}" if g.won else "x0"
        dt = g.created_at
        if dt and getattr(dt, "tzinfo", None) is None:
            pass
        stamp = dt.strftime("%m-%d %H:%M") if dt else "—"
        lines.append(
            f"{icon} {DIFFICULTIES.get(g.difficulty, {}).get('title', g.difficulty)} · "
            f"<b>{int(g.bet_amount)}</b> · {mult} · <code>{stamp}</code>"
        )
    return "\n".join(lines)


def build_stats_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    o = int(owner_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🃏 O‘ynash", f"jk:menu:{o}", "success")],
            [_btn("⬅️ Ortga", f"jk:back:{o}", "danger")],
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


def _reveal_deck_line(deck: list[float], pick: int) -> str:
    parts: list[str] = []
    for i, m in enumerate(deck):
        if i == pick:
            if m > 0:
                parts.append(f"<b>[{i+1}:x{m:g}]</b>")
            else:
                parts.append(f"<b>[{i+1}:💀]</b>")
        else:
            if m > 0:
                parts.append(f"{i+1}:x{m:g}")
            else:
                parts.append(f"{i+1}:·")
    return "\n🗂 Koloda: " + " ".join(parts)


class JokerCardsEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    def set_difficulty_pref(self, telegram_id: int, difficulty: str) -> None:
        _DIFF_PREF[int(telegram_id)] = normalize_diff(difficulty)

    def get_difficulty_pref(self, telegram_id: int) -> str:
        return _DIFF_PREF.get(int(telegram_id), "normal")

    async def get_balance(self, telegram_id: int) -> int:
        async with self.session_factory() as session:
            bal = await session.scalar(select(User.dollar).where(User.telegram_id == int(telegram_id)))
            return int(bal or 0)

    async def home(self, telegram_id: int, owner_id: int | None = None) -> JokerView:
        bal = await self.get_balance(telegram_id)
        oid = int(owner_id or telegram_id)
        diff = self.get_difficulty_pref(telegram_id)
        return JokerView(home_text(bal, diff), build_home_keyboard(oid, bal, diff))

    async def resolve_bet(
        self,
        telegram_id: int,
        owner_id: int,
        kind: str,
        amount: int | None = None,
    ) -> JokerView:
        bal = await self.get_balance(telegram_id)
        diff = self.get_difficulty_pref(telegram_id)
        if kind == "bet" and amount is not None:
            bet = int(amount)
        elif kind == "min":
            bet = JOKER_MIN_BET
        elif kind == "max":
            bet = min(JOKER_MAX_BET, max(0, bal))
        elif kind == "half":
            bet = max(0, bal // 2)
        elif kind == "all":
            bet = max(0, bal)
        else:
            return await self.home(telegram_id, owner_id)

        ok, err = validate_bet(bet, bal)
        if not ok:
            return JokerView(
                f"❌ {err}\n\n{home_text(bal, diff)}",
                build_home_keyboard(owner_id, bal, diff),
                "Stavka rad etildi.",
                True,
            )
        return JokerView(pick_text(bal, bet, diff), build_pick_keyboard(owner_id, bet, diff))

    async def show_stats(self, telegram_id: int, owner_id: int) -> JokerView:
        async with self.session_factory() as session:
            user = await session.scalar(select(User).where(User.telegram_id == int(telegram_id)))
            stats = await session.get(JokerCardsUserStats, int(telegram_id))
            bal = int(user.dollar or 0) if user else 0
        return JokerView(stats_text(stats, bal), build_stats_keyboard(owner_id))

    async def show_history(self, telegram_id: int, owner_id: int) -> JokerView:
        async with self.session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(JokerCardsGame)
                        .where(JokerCardsGame.user_telegram_id == int(telegram_id))
                        .order_by(JokerCardsGame.id.desc())
                        .limit(12)
                    )
                )
                .scalars()
                .all()
            )
        return JokerView(history_text(rows), build_stats_keyboard(owner_id))

    async def play(
        self,
        tg_user: TelegramUser,
        chat_id: int,
        bet_amount: int,
        difficulty: str,
        pick_index: int,
    ) -> JokerView:
        telegram_id = int(tg_user.id)
        d = normalize_diff(difficulty)
        n = deck_stats(d)["total"]
        if pick_index < 0 or pick_index >= n:
            return JokerView("❌ Noto‘g‘ri karta.", None, "Karta noto‘g‘ri.", True)
        if telegram_id in _IN_FLIGHT:
            return JokerView("", None, "⏳ O‘yin hali tugamadi…", True)

        lock = _user_lock(telegram_id)
        if lock.locked():
            return JokerView("", None, "⏳ Biroz kuting…", True)

        async with lock:
            if telegram_id in _IN_FLIGHT:
                return JokerView("", None, "⏳ O‘yin hali tugamadi…", True)
            _IN_FLIGHT.add(telegram_id)
            try:
                return await self._play_locked(tg_user, chat_id, int(bet_amount), d, int(pick_index))
            finally:
                _IN_FLIGHT.discard(telegram_id)

    async def _play_locked(
        self,
        tg_user: TelegramUser,
        chat_id: int,
        bet_amount: int,
        difficulty: str,
        pick_index: int,
    ) -> JokerView:
        async with self.session_factory() as session:
            async with session.begin():
                user = await self._get_or_create_user(session, tg_user)
                bal = int(user.dollar or 0)
                ok, err = validate_bet(bet_amount, bal)
                if not ok:
                    return JokerView(
                        f"❌ {err}",
                        build_home_keyboard(int(tg_user.id), bal, difficulty),
                        "Stavka rad etildi.",
                        True,
                    )

                deck = build_deck(difficulty)
                if pick_index < 0 or pick_index >= len(deck):
                    return JokerView("❌ Noto‘g‘ri karta.", None, "Karta noto‘g‘ri.", True)

                mult = float(deck[pick_index])
                won = mult > 0
                payout = int(bet_amount * mult) if won else 0
                profit = payout - bet_amount

                user.dollar = bal - bet_amount
                if won and payout > 0:
                    user.dollar = int(user.dollar) + payout
                balance_after = int(user.dollar)

                import json

                game = JokerCardsGame(
                    user_id=int(user.id),
                    user_telegram_id=int(user.telegram_id),
                    chat_id=int(chat_id),
                    bet_amount=bet_amount,
                    difficulty=difficulty,
                    pick_index=pick_index,
                    multiplier=mult,
                    won=won,
                    payout=payout,
                    profit=profit,
                    balance_after=balance_after,
                    deck_json=json.dumps(deck),
                    status="completed",
                    token=secrets.token_hex(8),
                )
                session.add(game)
                await session.flush()

                _record_dollar(
                    session,
                    user,
                    -bet_amount,
                    "joker_bet",
                    f"JokerCards #{game.id} {difficulty} pick={pick_index}",
                    chat_id,
                )
                if won and payout > 0:
                    _record_dollar(
                        session,
                        user,
                        payout,
                        "joker_win",
                        f"JokerCards yutuq #{game.id} x{mult:g}",
                        chat_id,
                    )

                session.add(
                    GameHistory(
                        user_id=int(user.id),
                        game_type=JOKER_GAME_TYPE,
                        bet_amount=bet_amount,
                        result="won" if won else "lost",
                        multiplier=mult if won else 0.0,
                        win_amount=payout if won else 0,
                    )
                )
                await self._update_stats(session, int(user.telegram_id), bet_amount, won, profit, payout)

                logger.info(
                    "joker user=%s game=%s bet=%s diff=%s pick=%s mult=%s won=%s bal=%s",
                    tg_user.id,
                    game.id,
                    bet_amount,
                    difficulty,
                    pick_index,
                    mult,
                    won,
                    balance_after,
                )

                reveal = _reveal_deck_line(deck, pick_index)
                return JokerView(
                    result_text(
                        bet=bet_amount,
                        difficulty=difficulty,
                        mult=mult,
                        won=won,
                        profit=profit,
                        balance=balance_after,
                        pick_index=pick_index,
                        deck_size=len(deck),
                        reveal_line=reveal,
                    ),
                    build_result_keyboard(int(tg_user.id)),
                    "🏆 Yutdingiz!" if won else "💀 Joker!",
                    True,
                    played=True,
                    won=won,
                    bet=bet_amount,
                    difficulty=difficulty,
                    multiplier=mult,
                    profit=profit,
                    balance=balance_after,
                    game_id=int(game.id),
                    pick_index=pick_index,
                    deck_size=len(deck),
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
        stats = await session.get(JokerCardsUserStats, int(telegram_id))
        if stats is None:
            stats = JokerCardsUserStats(user_telegram_id=int(telegram_id))
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

