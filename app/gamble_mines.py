from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import DollarTransaction, GambleMinesGame, GambleUserStats, User


GRID_SIZE = 36
GRID_WIDTH = 6
PICKS_PER_PLAYER = 3
DUEL_MINE_COUNT = 6
HOUSE_COMMISSION_RATE = 0.10
MIN_BET = 10
DAILY_WIN_LIMIT = 50_000
COOLDOWN_SECONDS = 20
MONEY_EMOJI_ID = "5409048419211682843"
GIFT_EMOJI_ID = "5199749070830197566"
BANK_EMOJI_ID = "5264895611517300926"
MINE_EMOJI_ID = "5469654973308476699"
_GAME_LOCKS: dict[int, asyncio.Lock] = {}


def _ce(symbol: str, emoji_id: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{symbol}</tg-emoji>'


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


class MinesRenderer:
    @staticmethod
    def keyboard(game: GambleMinesGame) -> InlineKeyboardMarkup | None:
        if game.status == "waiting":
            return InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🎮 Qimorga qo'shilish", callback_data=f"gm:j:{game.id}:{game.token}")]
                ]
            )
        if game.status != "active":
            return None

        state = _state(game)
        picks = _picks(game)
        picked_cells = _picked_cells(picks)
        rows: list[list[InlineKeyboardButton]] = []
        for row_idx in range(GRID_WIDTH):
            row: list[InlineKeyboardButton] = []
            for col_idx in range(GRID_WIDTH):
                cell = row_idx * GRID_WIDTH + col_idx
                opened = picked_cells.get(cell)
                if opened is not None:
                    text = "💣" if opened.get("mine") else f"{int(opened.get('value') or 0):02d}"
                    callback = f"gm:noop:{game.id}:{game.token}:{cell}"
                else:
                    text = "⬜"
                    callback = f"gm:p:{game.id}:{game.token}:{cell}"
                row.append(InlineKeyboardButton(text=text, callback_data=callback))
            rows.append(row)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def text(game: GambleMinesGame, *, result: str = "") -> str:
        state = _state(game)
        picks = _picks(game)
        players = _players(game, state)
        creator_id = players[0] if players else int(game.user_telegram_id)
        opponent_id = players[1] if len(players) > 1 else int(game.opponent_telegram_id or 0)
        creator_name = _name(state, creator_id)
        opponent_name = _name(state, opponent_id) if opponent_id else "Ikkinchi o'yinchi"
        creator_link = _user_link(creator_id, creator_name)
        opponent_link = _user_link(opponent_id, opponent_name) if opponent_id else escape(opponent_name)
        creator_sum = _score(picks, creator_id)
        opponent_sum = _score(picks, opponent_id) if opponent_id else 0
        creator_count = len(_player_picks(picks, creator_id))
        opponent_count = len(_player_picks(picks, opponent_id)) if opponent_id else 0
        creator_mined = _player_mined(picks, creator_id)
        opponent_mined = _player_mined(picks, opponent_id) if opponent_id else False

        if game.status == "waiting":
            return (
                f"🎲 <b>2 kishilik Qimor</b>\n"
                "━━━━━━━━━━━━━━━\n"
                f"{_ce('💵', MONEY_EMOJI_ID)} Stavka: <b>{int(game.bet)}</b> dollar\n"
                f"👤 Yaratuvchi: {creator_link}\n"
                "👥 Kerak: <b>2 ta o'yinchi</b>\n"
                "━━━━━━━━━━━━━━━\n"
                "Ikkinchi o'yinchi qo'shilsa, o'yin avtomatik boshlanadi."
            )

        if game.status == "cashed":
            winner_id = int(game.winner_telegram_id or 0)
            winner_name = _name(state, winner_id)
            winner = _user_link(winner_id, winner_name) if winner_id else "G'olib"
            pot = int(game.bet) * 2
            commission = max(0, pot - int(game.payout or 0))
            return (
                f"🏆 <b>Qimor yakunlandi</b>\n"
                "━━━━━━━━━━━━━━━\n"
                f"🥇 G'olib: {winner}\n"
                f"{_ce('💵', MONEY_EMOJI_ID)} Yutuq: <b>{int(game.payout or 0)}</b> dollar\n"
                f"🏦 Komissiya: <b>{commission}</b> dollar\n"
                f"🔢 Hisob: <b>{creator_sum}</b> : <b>{opponent_sum}</b>\n"
                "━━━━━━━━━━━━━━━"
            )

        if game.status == "draw":
            return (
                "🤝 <b>Qimor durrang tugadi</b>\n"
                "━━━━━━━━━━━━━━━\n"
                f"{creator_link}: <b>{creator_sum}</b>\n"
                f"{opponent_link}: <b>{opponent_sum}</b>\n"
                f"{_ce('💵', MONEY_EMOJI_ID)} Stavkalar qaytarildi.\n"
                "━━━━━━━━━━━━━━━"
            )

        if game.status == "lost":
            return (
                f"{_ce('💣', MINE_EMOJI_ID)} <b>Qimor yakunlandi</b>\n"
                "━━━━━━━━━━━━━━━\n"
                "Ikkala ishtirokchi ham minani ochdi.\n"
                f"{_ce('💵', MONEY_EMOJI_ID)} Bank: <b>{int(game.bet) * 2}</b> dollar kuyib ketdi.\n"
                "━━━━━━━━━━━━━━━"
            )

        turn_id = int(state.get("turn") or creator_id)
        turn_name = _name(state, turn_id)
        footer = result or "Navbatdagi o'yinchi bitta katak tanlaydi."
        return (
            f"🎲 <b>2 kishilik Qimor</b>\n"
            "━━━━━━━━━━━━━━━\n"
            f"{_ce('💵', MONEY_EMOJI_ID)} Stavka: <b>{int(game.bet)}</b> dollar\n"
            f"🏦 Bank: <b>{int(game.bet) * 2}</b> dollar\n"
            f"{_ce('💣', MINE_EMOJI_ID)} Mina: <b>yashirin</b>\n"
            f"🔵 {creator_link}: <b>{creator_sum}</b> ({creator_count}/{PICKS_PER_PLAYER}){' 💣' if creator_mined else ''}\n"
            f"🔴 {opponent_link}: <b>{opponent_sum}</b> ({opponent_count}/{PICKS_PER_PLAYER}){' 💣' if opponent_mined else ''}\n"
            f"👉 Navbat: {_user_link(turn_id, turn_name)}\n"
            "━━━━━━━━━━━━━━━\n"
            f"{footer}"
        )


class MinesEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def start_or_resume(self, tg_user, chat_id: int, raw_amount: str | None) -> MinesView:
        async with self.session_factory() as session:
            user = await self._ensure_user(session, tg_user)
            active = await self._active_game(session, tg_user.id)
            if active is not None and not _is_duel_game(active):
                await self._refund_legacy_game(session, active)
                active = None

            if active is not None:
                active.chat_id = chat_id
                active.last_action_at = _utcnow()
                await session.commit()
                return MinesView(
                    MinesRenderer.text(active, result="Davom etayotgan qimoringiz tiklandi."),
                    MinesRenderer.keyboard(active),
                    "Davom etayotgan o'yin ochildi.",
                    False,
                    int(active.id),
                    active.token,
                )

            ok, bet, error = MinesAntiCheatValidator.validate_bet(raw_amount)
            if not ok:
                return MinesView(error, None, error, True)

            stats = await self._stats(session, tg_user.id)
            now = _utcnow()
            if stats.last_started_at and _ensure_aware(stats.last_started_at) + timedelta(seconds=COOLDOWN_SECONDS) > now:
                wait = int((_ensure_aware(stats.last_started_at) + timedelta(seconds=COOLDOWN_SECONDS) - now).total_seconds())
                return MinesView(f"⏳ Keyingi qimorgacha <b>{wait}</b> sekund kuting.", None, "Cooldown hali tugamadi.", True)

            if int(user.dollar or 0) < bet:
                return MinesView("Balans yetarli emas.", None, "Balans yetarli emas.", True)

            user.dollar = int(user.dollar or 0) - bet
            stats.last_started_at = now
            stats.total_bet = int(stats.total_bet or 0) + bet
            state = _new_state(tg_user.id, getattr(tg_user, "full_name", None) or user.display_name or "User")
            game = GambleMinesGame(
                user_telegram_id=tg_user.id,
                opponent_telegram_id=None,
                winner_telegram_id=None,
                chat_id=chat_id,
                bet=bet,
                mine_count=0,
                mines_json=json.dumps(state, ensure_ascii=False),
                opened_json=json.dumps({"picks": {}, "order": []}, ensure_ascii=False),
                status="waiting",
                game_kind="duel",
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
                "gamble_duel_bet",
                note=f"Qimor duel stavka #{game.id}",
                chat_id=chat_id,
            )
            await session.commit()
            return MinesView(
                MinesRenderer.text(game),
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
            if game and game.message_id != message_id:
                game.message_id = message_id
                await session.commit()

    async def join(self, tg_user, game_id: int, token: str) -> MinesView:
        lock = _game_lock(game_id)
        await lock.acquire()
        try:
            async with self.session_factory() as session:
                game = await self._game_for_update(session, game_id, token)
                if game is None or not _is_duel_game(game):
                    return MinesView("", None, "O'yin topilmadi yoki eskirgan.", True)
                if game.status != "waiting":
                    return MinesView(MinesRenderer.text(game), MinesRenderer.keyboard(game), "Bu o'yin allaqachon boshlangan.", True)
                if int(game.user_telegram_id) == int(tg_user.id):
                    return MinesView("", None, "O'z o'yiningizga ikkinchi o'yinchi bo'lib kira olmaysiz.", True)
                active = await self._active_game(session, tg_user.id)
                if active is not None and int(active.id) != int(game.id):
                    return MinesView("", None, "Sizda boshqa davom etayotgan qimor bor.", True)

                user = await self._ensure_user(session, tg_user)
                if int(user.dollar or 0) < int(game.bet):
                    return MinesView("", None, "Balans yetarli emas.", True)

                now = _utcnow()
                stats = await self._stats(session, tg_user.id)
                user.dollar = int(user.dollar or 0) - int(game.bet)
                stats.total_bet = int(stats.total_bet or 0) + int(game.bet)
                state = _state(game)
                state["players"] = [int(game.user_telegram_id), int(tg_user.id)]
                state["turn"] = int(game.user_telegram_id)
                state["values"] = [secrets.randbelow(99) + 1 for _ in range(GRID_SIZE)]
                state["mines"] = sorted(secrets.SystemRandom().sample(range(GRID_SIZE), DUEL_MINE_COUNT))
                state["mine_pending"] = 0
                state.setdefault("names", {})[str(tg_user.id)] = (getattr(tg_user, "full_name", None) or user.display_name or "User")[:255]
                game.opponent_telegram_id = int(tg_user.id)
                game.status = "active"
                game.mines_json = json.dumps(state, ensure_ascii=False)
                game.last_action_at = now
                MinesEconomyService.record_dollar(
                    session,
                    user,
                    -int(game.bet),
                    "gamble_duel_bet",
                    note=f"Qimor duelga qo'shildi #{game.id}",
                    chat_id=game.chat_id,
                )
                await session.commit()
                return MinesView(
                    MinesRenderer.text(game, result="O'yin boshlandi. Har bir o'yinchi 3 tadan katak tanlaydi."),
                    MinesRenderer.keyboard(game),
                    "O'yin boshlandi.",
                    False,
                )
        finally:
            lock.release()

    async def open_cell(self, tg_user_id: int, game_id: int, token: str, cell: int) -> MinesView:
        if cell < 0 or cell >= GRID_SIZE:
            return MinesView("", None, "Katak noto'g'ri.", True)
        lock = _game_lock(game_id)
        await lock.acquire()
        try:
            async with self.session_factory() as session:
                game = await self._game_for_update(session, game_id, token)
                if game is None or not _is_duel_game(game):
                    return MinesView("", None, "O'yin topilmadi yoki eskirgan.", True)
                if game.status == "waiting":
                    return MinesView("", None, "Ikkinchi o'yinchi qo'shilmagan.", True)
                if game.status != "active":
                    return MinesView(MinesRenderer.text(game), None, "Bu o'yin yakunlangan.", True)

                state = _state(game)
                picks = _picks(game)
                players = _players(game, state)
                if int(tg_user_id) not in players:
                    return MinesView("", None, "Bu boshqa o'yinchilarning qimori.", True)
                if int(state.get("turn") or 0) != int(tg_user_id):
                    return MinesView("", None, "Hozir sizning navbatingiz emas.", True)
                if cell in _picked_cells(picks):
                    return MinesView("", None, "Bu katak allaqachon tanlangan.", True)
                pending_mine_user = int(state.get("mine_pending") or 0)
                is_mine_response = pending_mine_user and pending_mine_user != int(tg_user_id)
                if len(_player_picks(picks, tg_user_id)) >= PICKS_PER_PLAYER and not is_mine_response:
                    return MinesView("", None, "Siz 3 ta imkoniyatdan foydalandingiz.", True)

                values = state.get("values")
                if not isinstance(values, list) or len(values) < GRID_SIZE:
                    state["values"] = [secrets.randbelow(99) + 1 for _ in range(GRID_SIZE)]
                    values = state["values"]
                mines = _mine_cells(state)
                is_mine = cell in mines
                value = 0 if is_mine else int(values[cell])
                player_key = str(tg_user_id)
                picks.setdefault("picks", {}).setdefault(player_key, []).append({"cell": cell, "value": value, "mine": is_mine})
                picks.setdefault("order", []).append({"user": int(tg_user_id), "cell": cell, "value": value, "mine": is_mine})

                now = _utcnow()
                game.opened_json = json.dumps(picks, ensure_ascii=False)
                game.last_action_at = now
                if is_mine and pending_mine_user and pending_mine_user != int(tg_user_id):
                    state["mine_pending"] = 0
                    await self._finish_duel(session, game, players, state, picks, now)
                    await session.commit()
                    return MinesView(MinesRenderer.text(game), None, "Ikkala o'yinchi ham mina ochdi.", True)
                if is_mine:
                    next_player = _other_player(players, tg_user_id)
                    if len(_player_picks(picks, next_player)) >= PICKS_PER_PLAYER:
                        state["mine_pending"] = 0
                        await self._finish_duel(session, game, players, state, picks, now)
                        await session.commit()
                        return MinesView(MinesRenderer.text(game), None, "Mina ochildi. O'yin yakunlandi.", True)
                    state["mine_pending"] = int(tg_user_id)
                    state["turn"] = next_player
                    game.mines_json = json.dumps(state, ensure_ascii=False)
                    await session.commit()
                    return MinesView(
                        MinesRenderer.text(game, result="💣 Mina ochildi. Endi raqib bitta katak tanlaydi."),
                        MinesRenderer.keyboard(game),
                        "💣 Mina! Raqibga navbat berildi.",
                        True,
                    )
                if pending_mine_user and pending_mine_user != int(tg_user_id):
                    state["mine_pending"] = 0
                    await self._finish_duel(session, game, players, state, picks, now)
                    await session.commit()
                    return MinesView(MinesRenderer.text(game), None, "Safe ochildi. O'yin yakunlandi.", True)

                finished = all(len(_player_picks(picks, player_id)) >= PICKS_PER_PLAYER for player_id in players)
                result = f"Katak ochildi: <b>{value}</b>."
                if finished:
                    await self._finish_duel(session, game, players, state, picks, now)
                    await session.commit()
                    return MinesView(MinesRenderer.text(game), None, "O'yin yakunlandi.", True)

                next_player = _next_player(players, picks, tg_user_id)
                state["turn"] = next_player
                game.mines_json = json.dumps(state, ensure_ascii=False)
                await session.commit()
                return MinesView(MinesRenderer.text(game, result=result), MinesRenderer.keyboard(game), f"Katak: {value}", False)
        finally:
            lock.release()

    async def cashout(self, tg_user_id: int, game_id: int, token: str) -> MinesView:
        return MinesView("", None, "Yangi qimorda pulni olish tugmasi ishlatilmaydi.", True)

    async def weekly_top_text(self, limit: int = 10) -> str:
        now = _utcnow()
        since = now - timedelta(days=7)
        winner_expr = func.coalesce(GambleMinesGame.winner_telegram_id, GambleMinesGame.user_telegram_id).label("winner_id")
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        winner_expr,
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
                    .group_by(winner_expr)
                    .order_by(desc("total_won"), desc("best_win"))
                    .limit(max(1, min(30, int(limit))))
                )
            ).all()
            users = {}
            if rows:
                user_ids = [int(row.winner_id) for row in rows]
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
                "Hali bu haftada yutuq olgan qimorvoz yo'q."
            )

        lines: list[str] = []
        for idx, row in enumerate(rows, 1):
            telegram_id = int(row.winner_id)
            user = users.get(telegram_id)
            name = user.display_name if user else str(telegram_id)
            total_won = int(row.total_won or 0)
            games_count = int(row.games_count or 0)
            best_win = int(row.best_win or 0)
            lines.append(
                f"{idx}. {_user_link(telegram_id, name)} — <b>{total_won}</b> {_ce('💵', MONEY_EMOJI_ID)}\n"
                f"   🎮 G'alaba: <b>{games_count}</b> | 🔥 Eng katta: <b>{best_win}</b>"
            )

        return (
            "🏆 <b>Haftalik eng yaxshi qimorvozlar</b>\n"
            "━━━━━━━━━━━━━━━\n"
            + "\n".join(lines)
            + "\n━━━━━━━━━━━━━━━\n"
            "📌 Hisob: oxirgi 7 kun ichida 2 kishilik qimorda yutilgan umumiy pul."
        )

    async def _finish_duel(
        self,
        session: AsyncSession,
        game: GambleMinesGame,
        players: list[int],
        state: dict,
        picks: dict,
        now: datetime,
    ) -> None:
        first_id, second_id = players[0], players[1]
        first_score = _score(picks, first_id)
        second_score = _score(picks, second_id)
        first_mined = _player_mined(picks, first_id)
        second_mined = _player_mined(picks, second_id)
        first_user = await self._user(session, first_id)
        second_user = await self._user(session, second_id)
        first_stats = await self._stats(session, first_id)
        second_stats = await self._stats(session, second_id)
        game.ended_at = now
        game.last_action_at = now
        game.mines_json = json.dumps(state, ensure_ascii=False)
        game.opened_json = json.dumps(picks, ensure_ascii=False)

        if first_mined and second_mined:
            game.status = "lost"
            game.payout = 0
            game.winner_telegram_id = None
            first_stats.win_streak = 0
            second_stats.win_streak = 0
            return

        if first_mined != second_mined:
            winner_id = second_id if first_mined else first_id
            loser_id = first_id if first_mined else second_id
            await self._apply_duel_winner(
                session,
                game,
                winner_id,
                loser_id,
                first_user,
                second_user,
                first_stats,
                second_stats,
                now,
            )
            return

        if first_score == second_score:
            game.status = "draw"
            game.payout = 0
            for user in (first_user, second_user):
                if user is None:
                    continue
                user.dollar = int(user.dollar or 0) + int(game.bet)
                MinesEconomyService.record_dollar(
                    session,
                    user,
                    int(game.bet),
                    "gamble_duel_refund",
                    note=f"Qimor duel durrang #{game.id}",
                    chat_id=game.chat_id,
                )
            return

        winner_id = first_id if first_score > second_score else second_id
        loser_id = second_id if winner_id == first_id else first_id
        await self._apply_duel_winner(
            session,
            game,
            winner_id,
            loser_id,
            first_user,
            second_user,
            first_stats,
            second_stats,
            now,
        )

    async def _apply_duel_winner(
        self,
        session: AsyncSession,
        game: GambleMinesGame,
        winner_id: int,
        loser_id: int,
        first_user: Optional[User],
        second_user: Optional[User],
        first_stats: GambleUserStats,
        second_stats: GambleUserStats,
        now: datetime,
    ) -> None:
        first_id = int(game.user_telegram_id)
        second_id = int(game.opponent_telegram_id or 0)
        winner = first_user if winner_id == first_id else second_user
        winner_stats = first_stats if winner_id == first_id else second_stats
        loser_stats = second_stats if loser_id == second_id else first_stats
        pot = int(game.bet) * 2
        payout = int(pot * (1 - HOUSE_COMMISSION_RATE))
        today_won = await self._today_won(session, winner_id, now)
        if today_won + payout > DAILY_WIN_LIMIT:
            payout = max(0, DAILY_WIN_LIMIT - today_won)

        game.status = "cashed"
        game.winner_telegram_id = winner_id
        game.payout = payout
        winner_stats.win_streak = int(winner_stats.win_streak or 0) + 1
        winner_stats.total_payout = int(winner_stats.total_payout or 0) + payout
        loser_stats.win_streak = 0
        if winner is not None and payout > 0:
            winner.dollar = int(winner.dollar or 0) + payout
            MinesEconomyService.record_dollar(
                session,
                winner,
                payout,
                "gamble_duel_win",
                note=f"Qimor duel g'alaba #{game.id}: bank {pot}, komissiya {pot - payout}",
                chat_id=game.chat_id,
            )

    async def _refund_legacy_game(self, session: AsyncSession, game: GambleMinesGame) -> None:
        user = await self._user(session, int(game.user_telegram_id))
        if user is not None and game.status == "active":
            user.dollar = int(user.dollar or 0) + int(game.bet or 0)
            MinesEconomyService.record_dollar(
                session,
                user,
                int(game.bet or 0),
                "gamble_legacy_refund",
                note=f"Eski mines qimori qaytarildi #{game.id}",
                chat_id=game.chat_id,
            )
        game.status = "draw"
        game.ended_at = _utcnow()
        game.last_action_at = _utcnow()
        await session.commit()

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
                .where(
                    GambleMinesGame.status.in_(("waiting", "active")),
                    or_(
                        GambleMinesGame.user_telegram_id == telegram_id,
                        GambleMinesGame.opponent_telegram_id == telegram_id,
                    ),
                )
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
                func.coalesce(GambleMinesGame.winner_telegram_id, GambleMinesGame.user_telegram_id) == telegram_id,
                GambleMinesGame.status == "cashed",
                GambleMinesGame.ended_at.is_not(None),
                GambleMinesGame.ended_at >= start,
            )
        )
        return int(value or 0)


def _new_state(creator_id: int, creator_name: str) -> dict:
    return {
        "players": [int(creator_id)],
        "turn": int(creator_id),
        "values": [],
        "mines": [],
        "mine_pending": 0,
        "names": {str(creator_id): creator_name[:255]},
    }


def _state(game: GambleMinesGame) -> dict:
    try:
        parsed = json.loads(game.mines_json or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    parsed.setdefault("players", [int(game.user_telegram_id)])
    parsed.setdefault("turn", int(game.user_telegram_id))
    parsed.setdefault("values", [])
    parsed.setdefault("mines", [])
    parsed.setdefault("mine_pending", 0)
    parsed.setdefault("names", {})
    return parsed


def _picks(game: GambleMinesGame) -> dict:
    try:
        parsed = json.loads(game.opened_json or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    parsed.setdefault("picks", {})
    parsed.setdefault("order", [])
    return parsed


def _players(game: GambleMinesGame, state: dict) -> list[int]:
    raw_players = state.get("players")
    players: list[int] = []
    if isinstance(raw_players, list):
        for item in raw_players[:2]:
            try:
                players.append(int(item))
            except (TypeError, ValueError):
                continue
    if not players:
        players.append(int(game.user_telegram_id))
    if game.opponent_telegram_id and int(game.opponent_telegram_id) not in players:
        players.append(int(game.opponent_telegram_id))
    return players[:2]


def _name(state: dict, user_id: int) -> str:
    if not user_id:
        return "O'yinchi"
    names = state.get("names") if isinstance(state.get("names"), dict) else {}
    return str(names.get(str(user_id)) or user_id)


def _player_picks(picks: dict, user_id: int) -> list[dict]:
    if not user_id:
        return []
    values = picks.get("picks", {}).get(str(user_id), [])
    return values if isinstance(values, list) else []


def _score(picks: dict, user_id: int) -> int:
    total = 0
    for item in _player_picks(picks, user_id):
        if not isinstance(item, dict):
            continue
        if item.get("mine"):
            continue
        try:
            total += int(item.get("value") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _player_mined(picks: dict, user_id: int) -> bool:
    return any(bool(item.get("mine")) for item in _player_picks(picks, user_id) if isinstance(item, dict))


def _mine_cells(state: dict) -> set[int]:
    raw = state.get("mines")
    result: set[int] = set()
    if not isinstance(raw, list):
        return result
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= value < GRID_SIZE:
            result.add(value)
    return result


def _picked_cells(picks: dict) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for values in (picks.get("picks") or {}).values():
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            try:
                cell = int(item.get("cell"))
                result[cell] = {"value": int(item.get("value") or 0), "mine": bool(item.get("mine"))}
            except (TypeError, ValueError):
                continue
    return result


def _other_player(players: list[int], current_id: int) -> int:
    for player_id in players:
        if int(player_id) != int(current_id):
            return int(player_id)
    return int(current_id)


def _next_player(players: list[int], picks: dict, current_id: int) -> int:
    for player_id in players:
        if player_id != int(current_id) and len(_player_picks(picks, player_id)) < PICKS_PER_PLAYER:
            return player_id
    for player_id in players:
        if len(_player_picks(picks, player_id)) < PICKS_PER_PLAYER:
            return player_id
    return int(current_id)


def _is_duel_game(game: GambleMinesGame) -> bool:
    try:
        parsed = json.loads(game.mines_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(parsed, dict):
        return False
    players = parsed.get("players")
    names = parsed.get("names")
    return (
        getattr(game, "game_kind", None) == "duel"
        and isinstance(players, list)
        and len(players) >= 1
        and isinstance(names, dict)
    )


def _user_link(user_id: int, name: str) -> str:
    if not user_id:
        return escape(name or "O'yinchi")
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
