from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User as TelegramUser
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gamble_guard import enforce_gamble_overwin_guard
from app.models import DollarTransaction, GameHistory, TreasureHuntGame, User

logger = logging.getLogger(__name__)

TREASURE_GAME_TYPE = "treasure_hunt"
TREASURE_GRID_WIDTH = 6
TREASURE_GRID_SIZE = 36
TREASURE_MINE_COUNT = 5
TREASURE_ROUND_SECONDS = 20
TREASURE_MIN_PLAYERS = 2
TREASURE_MAX_PLAYERS = 10
TREASURE_MIN_BET = 100
TREASURE_MAX_BET = 100_000
TREASURE_BET_OPTIONS = (100, 500, 1000, 5000, 10000)
TREASURE_WAITING = "waiting"
TREASURE_ACTIVE = "active"
TREASURE_FINISHED = "finished"
TREASURE_CANCELLED = "cancelled"
TREASURE_SEPARATOR = "━━━━━━━━━━━━━━━"
MONEY_EMOJI_ID = "5409048419211682843"
MINE_EMOJI_ID = "5469654973308476699"
_LOCKS: dict[int, asyncio.Lock] = {}


@dataclass(frozen=True)
class TreasureView:
    text: str
    keyboard: Optional[InlineKeyboardMarkup] = None
    alert: str = ""
    show_alert: bool = False
    game_id: Optional[int] = None
    token: str = ""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ce(symbol: str, emoji_id: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{symbol}</tg-emoji>'


def _button(text: str, callback_data: str, style: str = "success") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data, **{"style": style})


def _lock(game_id: int) -> asyncio.Lock:
    lock = _LOCKS.get(int(game_id))
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[int(game_id)] = lock
    return lock


def _loads(raw: str | None, fallback):
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _name(value: str) -> str:
    return escape(value or "User")


def _user_link(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={int(user_id)}">{_name(name)}</a>'


def _players(game: TreasureHuntGame) -> list[dict]:
    return _loads(game.players_json, [])


def _mines(game: TreasureHuntGame) -> set[int]:
    return {int(item) for item in _loads(game.mines_json, []) if str(item).isdigit()}


def _picks(game: TreasureHuntGame) -> dict[str, int]:
    raw = _loads(game.picks_json, {})
    result: dict[str, int] = {}
    for key, value in raw.items():
        try:
            result[str(int(key))] = int(value)
        except (TypeError, ValueError):
            continue
    return result


def _eliminated(game: TreasureHuntGame) -> list[dict]:
    return _loads(game.eliminated_json, [])


def _results(game: TreasureHuntGame) -> list[dict]:
    return _loads(game.results_json, [])


def _active_players(game: TreasureHuntGame) -> list[dict]:
    return [item for item in _players(game) if item.get("alive") is True]


def _generate_mines() -> list[int]:
    return sorted(secrets.SystemRandom().sample(range(TREASURE_GRID_SIZE), TREASURE_MINE_COUNT))


def _cell_label(cell: int) -> str:
    return f"{cell + 1:02d}"


def treasure_start_text() -> str:
    return (
        f"💎 <b>Treasure Hunt</b>\n"
        f"{TREASURE_SEPARATOR}\n"
        "6x6 yashil maydon. Har raundda 5 ta mina yashirinadi.\n"
        "20 sekund ichida katak tanlamaganlar avtomatik chiqadi.\n\n"
        "Stavkani tanlang:"
    )


def build_treasure_start_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("100", f"thnew:start:{owner_id}:100"),
                _button("500", f"thnew:start:{owner_id}:500"),
                _button("1000", f"thnew:start:{owner_id}:1000"),
            ],
            [
                _button("5000", f"thnew:start:{owner_id}:5000"),
                _button("10000", f"thnew:start:{owner_id}:10000"),
            ],
            [_button("⬅️ Ortga", f"qmenu:back:{owner_id}", "danger")],
        ]
    )


def parse_treasure_new_callback(data: str) -> tuple[str, int, Optional[int]]:
    parts = (data or "").split(":")
    if len(parts) < 3 or parts[0] != "thnew" or not parts[2].isdigit():
        raise ValueError("bad_callback")
    action = parts[1]
    owner_id = int(parts[2])
    amount = int(parts[3]) if len(parts) == 4 and parts[3].isdigit() else None
    return action, owner_id, amount


def parse_treasure_callback(data: str) -> tuple[str, int, str, Optional[int]]:
    parts = (data or "").split(":")
    if len(parts) < 4 or parts[0] != "th" or not parts[2].isdigit():
        raise ValueError("bad_callback")
    action = parts[1]
    game_id = int(parts[2])
    token = parts[3]
    cell = int(parts[4]) if len(parts) == 5 and parts[4].isdigit() else None
    return action, game_id, token, cell


def _waiting_text(game: TreasureHuntGame) -> str:
    players = _players(game)
    lines = [
        "💎 <b>Treasure Hunt</b>",
        TREASURE_SEPARATOR,
        f"{_ce('💵', MONEY_EMOJI_ID)} Stavka: <b>{int(game.bet_amount)}</b> dollar",
        f"🏦 Pool: <b>{int(game.pool_amount)}</b> dollar",
        f"👥 Ishtirokchilar: <b>{len(players)}</b>/<b>{TREASURE_MAX_PLAYERS}</b>",
        TREASURE_SEPARATOR,
    ]
    if players:
        lines.extend(f"{idx}. {_user_link(int(p['telegram_id']), str(p['name']))}" for idx, p in enumerate(players, 1))
    lines.extend([
        TREASURE_SEPARATOR,
        "Yaratuvchi <b>Start</b> bosganda o'yin boshlanadi.",
    ])
    return "\n".join(lines)


def _round_status_line(game: TreasureHuntGame) -> str:
    if not game.round_ends_at:
        return "⏳ Raund: <b>20s</b>"
    left = max(0, int((game.round_ends_at - _utcnow()).total_seconds()))
    return f"⏳ Raund: <b>{left}s</b>"


def _active_text(game: TreasureHuntGame, *, result: str = "") -> str:
    active = _active_players(game)
    picks = _picks(game)
    lines = [
        "💎 <b>Treasure Hunt</b>",
        TREASURE_SEPARATOR,
        f"🔁 Raund: <b>{int(game.round_number)}</b>",
        _round_status_line(game),
        f"👥 Tiriklar: <b>{len(active)}</b>",
        f"✅ Tanladi: <b>{len(picks)}</b>/<b>{len(active)}</b>",
        f"{_ce('💣', MINE_EMOJI_ID)} Mina: <b>{TREASURE_MINE_COUNT}</b> ta",
        TREASURE_SEPARATOR,
    ]
    if result:
        lines.extend([result, TREASURE_SEPARATOR])
    lines.append("Yashil kataklardan birini tanlang.")
    return "\n".join(lines)


def _finished_text(game: TreasureHuntGame) -> str:
    results = _results(game)
    lines = [
        "🏆 <b>Treasure Hunt yakunlandi</b>",
        TREASURE_SEPARATOR,
        f"🏦 Pool: <b>{int(game.pool_amount)}</b> dollar",
        TREASURE_SEPARATOR,
    ]
    if results:
        for idx, item in enumerate(results, 1):
            prize = int(item.get("prize") or 0)
            name = str(item.get("name") or "User")
            user_id = int(item.get("telegram_id") or 0)
            if prize > 0:
                lines.append(f"{idx}. {_user_link(user_id, name)} - <b>{prize}</b> dollar")
            else:
                lines.append(f"{idx}. {_user_link(user_id, name)} - 0")
        if all(int(item.get("prize") or 0) == 0 for item in results):
            lines.append(f"{_ce('💣', MINE_EMOJI_ID)} Hamma chiqib ketdi, pool kuyib ketdi.")
    else:
        lines.append("Natija topilmadi.")
    lines.append(TREASURE_SEPARATOR)
    return "\n".join(lines)


def _keyboard(game: TreasureHuntGame, *, reveal: bool = False) -> InlineKeyboardMarkup | None:
    if game.status == TREASURE_WAITING:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("✅ Qo'shilish", f"th:j:{game.id}:{game.token}")],
                [_button("▶️ Start", f"th:start:{game.id}:{game.token}")],
                [_button("❌ Bekor qilish", f"th:cancel:{game.id}:{game.token}", "danger")],
            ]
        )
    if game.status != TREASURE_ACTIVE:
        return None
    mines = _mines(game)
    picks = set(_picks(game).values())
    rows: list[list[InlineKeyboardButton]] = []
    for row_idx in range(TREASURE_GRID_WIDTH):
        row: list[InlineKeyboardButton] = []
        for col_idx in range(TREASURE_GRID_WIDTH):
            cell = row_idx * TREASURE_GRID_WIDTH + col_idx
            if reveal and cell in mines:
                row.append(_button("💣", f"th:noop:{game.id}:{game.token}:{cell}", "danger"))
            elif cell in picks:
                row.append(_button("✅", f"th:noop:{game.id}:{game.token}:{cell}", "success"))
            else:
                row.append(_button("🟩", f"th:p:{game.id}:{game.token}:{cell}", "success"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _reveal_keyboard(game: TreasureHuntGame, mines: set[int], picks: dict[str, int]) -> InlineKeyboardMarkup:
    picked = set(picks.values())
    rows: list[list[InlineKeyboardButton]] = []
    for row_idx in range(TREASURE_GRID_WIDTH):
        row: list[InlineKeyboardButton] = []
        for col_idx in range(TREASURE_GRID_WIDTH):
            cell = row_idx * TREASURE_GRID_WIDTH + col_idx
            if cell in mines:
                row.append(_button("💣", f"th:noop:{game.id}:{game.token}:{cell}", "danger"))
            elif cell in picked:
                row.append(_button("✅", f"th:noop:{game.id}:{game.token}:{cell}", "success"))
            else:
                row.append(_button("🟩", f"th:noop:{game.id}:{game.token}:{cell}", "success"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _view(game: TreasureHuntGame, *, result: str = "", reveal: bool = False) -> TreasureView:
    if game.status == TREASURE_WAITING:
        return TreasureView(_waiting_text(game), _keyboard(game), game_id=int(game.id), token=str(game.token))
    if game.status == TREASURE_ACTIVE:
        return TreasureView(_active_text(game, result=result), _keyboard(game, reveal=reveal), game_id=int(game.id), token=str(game.token))
    return TreasureView(_finished_text(game), None, game_id=int(game.id), token=str(game.token))


class TreasureHuntEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_game(self, tg_user: TelegramUser, chat_id: int, bet_amount: int) -> TreasureView:
        bet_amount = int(bet_amount)
        if bet_amount < TREASURE_MIN_BET or bet_amount > TREASURE_MAX_BET:
            return TreasureView(f"❌ Stavka <b>{TREASURE_MIN_BET}</b> dan <b>{TREASURE_MAX_BET}</b> dollargacha bo'lishi kerak.", build_treasure_start_keyboard(int(tg_user.id)), "Stavka noto'g'ri.", True)
        async with self.session_factory() as session:
            async with session.begin():
                user = await self._get_or_create_user(session, tg_user)
                active = await self._active_user_game(session, int(user.telegram_id))
                if active is not None:
                    return TreasureView(_view(active).text, _view(active).keyboard, "Sizda aktiv Treasure Hunt bor.", True, int(active.id), str(active.token))
                if int(user.dollar or 0) < bet_amount:
                    return TreasureView("❌ Balansingiz yetarli emas.", build_treasure_start_keyboard(int(tg_user.id)), "Balans yetarli emas.", True)
                user.dollar = max(0, int(user.dollar or 0) - bet_amount)
                player = self._player_dict(user)
                game = TreasureHuntGame(
                    creator_telegram_id=int(user.telegram_id),
                    chat_id=int(chat_id),
                    bet_amount=bet_amount,
                    pool_amount=bet_amount,
                    status=TREASURE_WAITING,
                    token=secrets.token_urlsafe(8),
                    players_json=_dumps([player]),
                    mines_json="[]",
                    picks_json="{}",
                    eliminated_json="[]",
                    results_json="[]",
                )
                session.add(game)
                await session.flush()
                self._record_dollar(session, user, -bet_amount, "treasure_bet", f"Treasure Hunt #{game.id}", chat_id)
                logger.info("treasure_created user=%s game=%s bet=%s", tg_user.id, game.id, bet_amount)
                return _view(game)

    async def set_message_id(self, game_id: int, token: str, message_id: int) -> None:
        async with self.session_factory() as session:
            game = await self._game_by_token(session, game_id, token)
            if game is None:
                return
            game.message_id = int(message_id)
            await session.commit()

    async def join(self, tg_user: TelegramUser, game_id: int, token: str) -> TreasureView:
        async with _lock(game_id):
            async with self.session_factory() as session:
                async with session.begin():
                    game = await self._game_for_update(session, game_id, token)
                    if game is None:
                        return TreasureView("", None, "O'yin topilmadi.", True)
                    if game.status != TREASURE_WAITING:
                        return TreasureView(_view(game).text, _view(game).keyboard, "O'yin allaqachon boshlangan.", True, int(game.id), str(game.token))
                    players = _players(game)
                    if any(int(p.get("telegram_id")) == int(tg_user.id) for p in players):
                        return TreasureView(_waiting_text(game), _keyboard(game), "Siz allaqachon qo'shilgansiz.", True, int(game.id), str(game.token))
                    if len(players) >= TREASURE_MAX_PLAYERS:
                        return TreasureView(_waiting_text(game), _keyboard(game), "Joylar to'lgan.", True, int(game.id), str(game.token))
                    user = await self._get_or_create_user(session, tg_user)
                    active = await self._active_user_game(session, int(user.telegram_id))
                    if active is not None and int(active.id) != int(game.id):
                        return TreasureView("", None, "Sizda boshqa aktiv Treasure Hunt bor.", True)
                    if int(user.dollar or 0) < int(game.bet_amount):
                        return TreasureView(_waiting_text(game), _keyboard(game), "Balansingiz yetarli emas.", True, int(game.id), str(game.token))
                    user.dollar = max(0, int(user.dollar or 0) - int(game.bet_amount))
                    players.append(self._player_dict(user))
                    game.players_json = _dumps(players)
                    game.pool_amount = int(game.pool_amount or 0) + int(game.bet_amount)
                    game.last_action_at = _utcnow()
                    self._record_dollar(session, user, -int(game.bet_amount), "treasure_bet", f"Treasure Hunt #{game.id}", int(game.chat_id))
                    logger.info("treasure_joined user=%s game=%s", tg_user.id, game.id)
                    return _view(game)

    async def start(self, tg_user_id: int, game_id: int, token: str) -> TreasureView:
        async with _lock(game_id):
            async with self.session_factory() as session:
                async with session.begin():
                    game = await self._game_for_update(session, game_id, token)
                    if game is None:
                        return TreasureView("", None, "O'yin topilmadi.", True)
                    if int(game.creator_telegram_id) != int(tg_user_id):
                        return TreasureView(_view(game).text, _view(game).keyboard, "Faqat o'yin yaratuvchisi start bera oladi.", True, int(game.id), str(game.token))
                    players = _players(game)
                    if len(players) < TREASURE_MIN_PLAYERS:
                        return TreasureView(_waiting_text(game), _keyboard(game), "Kamida 2 ta ishtirokchi kerak.", True, int(game.id), str(game.token))
                    game.status = TREASURE_ACTIVE
                    game.round_number = 1
                    game.mines_json = _dumps(_generate_mines())
                    game.picks_json = "{}"
                    game.round_ends_at = _utcnow() + timedelta(seconds=TREASURE_ROUND_SECONDS)
                    game.last_action_at = _utcnow()
                    logger.info("treasure_started game=%s players=%s", game.id, len(players))
                    return _view(game, result="1-raund boshlandi.")

    async def pick(self, tg_user_id: int, game_id: int, token: str, cell: int) -> TreasureView:
        if cell < 0 or cell >= TREASURE_GRID_SIZE:
            return TreasureView("", None, "Katak noto'g'ri.", True)
        async with _lock(game_id):
            async with self.session_factory() as session:
                async with session.begin():
                    game = await self._game_for_update(session, game_id, token)
                    if game is None:
                        return TreasureView("", None, "O'yin topilmadi.", True)
                    if game.status != TREASURE_ACTIVE:
                        return TreasureView(_view(game).text, _view(game).keyboard, "Bu raund aktiv emas.", True, int(game.id), str(game.token))
                    player = self._find_player(game, int(tg_user_id))
                    if player is None:
                        return TreasureView("", None, "Bu o'yinda siz yo'qsiz.", True)
                    if player.get("alive") is not True:
                        return TreasureView(_view(game).text, _view(game).keyboard, "Siz o'yindan chiqqansiz.", True, int(game.id), str(game.token))
                    picks = _picks(game)
                    if str(int(tg_user_id)) in picks:
                        return TreasureView(_view(game).text, _view(game).keyboard, "Bu raundda katak tanlab bo'lgansiz.", True, int(game.id), str(game.token))
                    picks[str(int(tg_user_id))] = int(cell)
                    game.picks_json = _dumps(picks)
                    game.last_action_at = _utcnow()
                    view = _view(game)
                    return TreasureView(view.text, view.keyboard, "✅ Katak tanlandi.", False, int(game.id), str(game.token))

    async def cancel(self, tg_user_id: int, game_id: int, token: str) -> TreasureView:
        async with _lock(game_id):
            async with self.session_factory() as session:
                async with session.begin():
                    game = await self._game_for_update(session, game_id, token)
                    if game is None:
                        return TreasureView("", None, "O'yin topilmadi.", True)
                    if int(game.creator_telegram_id) != int(tg_user_id):
                        return TreasureView(_view(game).text, _view(game).keyboard, "Faqat yaratuvchi bekor qila oladi.", True, int(game.id), str(game.token))
                    if game.status != TREASURE_WAITING:
                        return TreasureView(_view(game).text, _view(game).keyboard, "Boshlangan o'yinni bekor qilib bo'lmaydi.", True, int(game.id), str(game.token))
                    game.status = TREASURE_CANCELLED
                    game.ended_at = _utcnow()
                    await self._refund_waiting_game(session, game)
                    return TreasureView("❌ Treasure Hunt bekor qilindi. Stavkalar qaytarildi.", None, "Bekor qilindi.", False, int(game.id), str(game.token))

    async def resolve_due_rounds(self, bot: Bot, limit: int = 20) -> None:
        now = _utcnow()
        async with self.session_factory() as session:
            games = (
                await session.execute(
                    select(TreasureHuntGame)
                    .where(TreasureHuntGame.status == TREASURE_ACTIVE, TreasureHuntGame.round_ends_at <= now)
                    .order_by(TreasureHuntGame.round_ends_at.asc())
                    .limit(limit)
                )
            ).scalars().all()
        for item in games:
            await self._resolve_and_publish(bot, int(item.id), str(item.token))

    async def _resolve_and_publish(self, bot: Bot, game_id: int, token: str) -> None:
        async with _lock(game_id):
            reveal_text = ""
            reveal_markup = None
            next_text = ""
            next_markup = None
            guard_ids: list[int] = []
            chat_id = None
            message_id = None
            async with self.session_factory() as session:
                async with session.begin():
                    game = await self._game_for_update(session, game_id, token)
                    if game is None or game.status != TREASURE_ACTIVE:
                        return
                    chat_id = int(game.chat_id)
                    message_id = int(game.message_id or 0)
                    old_mines = _mines(game)
                    old_picks = _picks(game)
                    await self._resolve_locked(session, game)
                    guard_ids = [
                        int(item.get("telegram_id") or 0)
                        for item in _results(game)
                        if int(item.get("prize") or 0) > 0 and int(item.get("telegram_id") or 0) > 0
                    ]
                    reveal_text = _active_text(game, result=self._last_round_result_text(game)) if game.status == TREASURE_ACTIVE else _finished_text(game)
                    reveal_markup = _reveal_keyboard(game, old_mines, old_picks) if game.status == TREASURE_ACTIVE else None
                    next_text = _view(game).text
                    next_markup = _view(game).keyboard
            if not chat_id or not message_id:
                return
            await self._safe_edit(bot, chat_id, message_id, reveal_text, reveal_markup)
            if next_markup is not None and reveal_markup is not None:
                await asyncio.sleep(2)
                await self._safe_edit(bot, chat_id, message_id, next_text, next_markup)
            for user_id in guard_ids:
                await enforce_gamble_overwin_guard(self.session_factory, bot, user_id)

    async def _resolve_locked(self, session: AsyncSession, game: TreasureHuntGame) -> None:
        if game.status != TREASURE_ACTIVE:
            return
        mines = _mines(game)
        picks = _picks(game)
        players = _players(game)
        active = [p for p in players if p.get("alive") is True]
        eliminated = _eliminated(game)
        round_out: list[dict] = []
        for player in active:
            user_id = int(player.get("telegram_id"))
            picked = picks.get(str(user_id))
            reason = ""
            if picked is None:
                reason = "time"
            elif int(picked) in mines:
                reason = "mine"
            if reason:
                player["alive"] = False
                item = {
                    "telegram_id": user_id,
                    "name": player.get("name") or "User",
                    "round": int(game.round_number or 0),
                    "cell": picked,
                    "reason": reason,
                }
                eliminated.append(item)
                round_out.append(item)
        game.players_json = _dumps(players)
        game.eliminated_json = _dumps(eliminated)
        alive = [p for p in players if p.get("alive") is True]
        if len(alive) <= 1:
            await self._finish_locked(session, game, alive)
            return
        game.round_number = int(game.round_number or 0) + 1
        game.mines_json = _dumps(_generate_mines())
        game.picks_json = "{}"
        game.round_ends_at = _utcnow() + timedelta(seconds=TREASURE_ROUND_SECONDS)
        game.last_action_at = _utcnow()

    async def _finish_locked(self, session: AsyncSession, game: TreasureHuntGame, alive: list[dict]) -> None:
        players = _players(game)
        eliminated = _eliminated(game)
        by_id = {int(p.get("telegram_id")): p for p in players}
        final_order: list[dict] = []
        for item in eliminated:
            user_id = int(item.get("telegram_id") or 0)
            if user_id in by_id:
                final_order.append(by_id[user_id])
        for player in alive:
            if player not in final_order:
                final_order.append(player)
        if not alive:
            results = []
            for player in reversed(final_order):
                user_id = int(player.get("telegram_id"))
                results.append({"telegram_id": user_id, "name": player.get("name") or "User", "prize": 0})
                user = await self._user_by_telegram(session, user_id)
                if user is not None:
                    session.add(
                        GameHistory(
                            user_id=int(user.id),
                            game_type=TREASURE_GAME_TYPE,
                            bet_amount=int(game.bet_amount),
                            result="lost",
                            multiplier=1.0,
                            win_amount=0,
                        )
                    )
            game.results_json = _dumps(results)
            game.status = TREASURE_FINISHED
            game.ended_at = _utcnow()
            game.round_ends_at = None
            game.last_action_at = _utcnow()
            logger.info("treasure_finished_no_survivors game=%s players=%s pool=%s", game.id, len(players), int(game.pool_amount or 0))
            return
        paid_count = 1 if len(players) <= 4 else max(1, len(players) - 4)
        paid = final_order[-paid_count:]
        weights = list(range(1, len(paid) + 1))
        total_weight = sum(weights) or 1
        pool = int(game.pool_amount or 0)
        prizes: dict[int, int] = {}
        distributed = 0
        for player, weight in zip(paid, weights):
            amount = (pool * weight) // total_weight
            prizes[int(player.get("telegram_id"))] = amount
            distributed += amount
        if paid and distributed < pool:
            prizes[int(paid[-1].get("telegram_id"))] = prizes.get(int(paid[-1].get("telegram_id")), 0) + (pool - distributed)
        results: list[dict] = []
        for player in reversed(final_order):
            user_id = int(player.get("telegram_id"))
            prize = int(prizes.get(user_id, 0))
            results.append({"telegram_id": user_id, "name": player.get("name") or "User", "prize": prize})
            user = await self._user_by_telegram(session, user_id)
            if user is not None:
                if prize > 0:
                    user.dollar = int(user.dollar or 0) + prize
                    self._record_dollar(session, user, prize, "treasure_prize", f"Treasure Hunt #{game.id}", int(game.chat_id))
                session.add(GameHistory(user_id=int(user.id), game_type=TREASURE_GAME_TYPE, bet_amount=int(game.bet_amount), result="won" if prize > 0 else "lost", multiplier=1.0, win_amount=prize))
        game.results_json = _dumps(results)
        game.status = TREASURE_FINISHED
        game.ended_at = _utcnow()
        game.round_ends_at = None
        game.last_action_at = _utcnow()
        logger.info("treasure_finished game=%s players=%s pool=%s", game.id, len(players), pool)

    def _last_round_result_text(self, game: TreasureHuntGame) -> str:
        round_number = int(game.round_number or 1) - 1
        items = [item for item in _eliminated(game) if int(item.get("round") or 0) == round_number]
        if not items:
            return "Bu raundda hech kim chiqmadi."
        lines = ["Raund natijasi:"]
        for item in items:
            reason = "vaqtida tanlamadi" if item.get("reason") == "time" else f"{_ce('💣', MINE_EMOJI_ID)} mina bosdi"
            cell = item.get("cell")
            cell_text = "-" if cell is None else _cell_label(int(cell))
            lines.append(f"• {_user_link(int(item['telegram_id']), str(item['name']))} - {reason} ({cell_text})")
        return "\n".join(lines)

    async def _safe_edit(self, bot: Bot, chat_id: int, message_id: int, text: str, markup: Optional[InlineKeyboardMarkup]) -> None:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
        except (TelegramForbiddenError, TelegramBadRequest):
            return

    async def _refund_waiting_game(self, session: AsyncSession, game: TreasureHuntGame) -> None:
        for player in _players(game):
            user = await self._user_by_telegram(session, int(player.get("telegram_id")))
            if user is None:
                continue
            user.dollar = int(user.dollar or 0) + int(game.bet_amount)
            self._record_dollar(session, user, int(game.bet_amount), "treasure_refund", f"Treasure Hunt bekor #{game.id}", int(game.chat_id))

    async def _get_or_create_user(self, session: AsyncSession, tg_user: TelegramUser) -> User:
        user = await self._user_by_telegram(session, int(tg_user.id))
        display_name = (tg_user.full_name or tg_user.username or "User")[:255]
        if user is None:
            user = User(telegram_id=int(tg_user.id), username=tg_user.username, display_name=display_name, dollar=0)
            session.add(user)
            await session.flush()
        else:
            user.username = tg_user.username
            user.display_name = display_name
        return user

    async def _user_by_telegram(self, session: AsyncSession, telegram_id: int) -> Optional[User]:
        return await session.scalar(select(User).where(User.telegram_id == int(telegram_id)))

    def _player_dict(self, user: User) -> dict:
        return {"telegram_id": int(user.telegram_id), "user_id": int(user.id), "name": (user.display_name or "User")[:255], "alive": True}

    def _find_player(self, game: TreasureHuntGame, telegram_id: int) -> Optional[dict]:
        for item in _players(game):
            if int(item.get("telegram_id") or 0) == int(telegram_id):
                return item
        return None

    async def _active_user_game(self, session: AsyncSession, telegram_id: int) -> Optional[TreasureHuntGame]:
        pattern = f'"telegram_id":{int(telegram_id)}'
        return (
            await session.execute(
                select(TreasureHuntGame)
                .where(
                    TreasureHuntGame.status.in_((TREASURE_WAITING, TREASURE_ACTIVE)),
                    or_(
                        TreasureHuntGame.creator_telegram_id == int(telegram_id),
                        TreasureHuntGame.players_json.contains(pattern),
                    ),
                )
                .order_by(TreasureHuntGame.id.desc())
            )
        ).scalars().first()

    async def _game_by_token(self, session: AsyncSession, game_id: int, token: str) -> Optional[TreasureHuntGame]:
        return await session.scalar(select(TreasureHuntGame).where(TreasureHuntGame.id == int(game_id), TreasureHuntGame.token == str(token)))

    async def _game_for_update(self, session: AsyncSession, game_id: int, token: str) -> Optional[TreasureHuntGame]:
        return await session.scalar(select(TreasureHuntGame).where(TreasureHuntGame.id == int(game_id), TreasureHuntGame.token == str(token)).with_for_update())

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
                chat_id=int(chat_id),
            )
        )


async def start_treasure_game_message(message: Message, bet_amount: int, session_factory: async_sessionmaker[AsyncSession]) -> TreasureView:
    engine = TreasureHuntEngine(session_factory)
    view = await engine.create_game(message.from_user, message.chat.id, bet_amount)
    sent = await message.answer(view.text, reply_markup=view.keyboard)
    if view.game_id and view.token:
        await engine.set_message_id(view.game_id, view.token, sent.message_id)
    return view
