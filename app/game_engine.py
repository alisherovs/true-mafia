from __future__ import annotations

from typing import Optional, Union
import json
import logging
import asyncio
import unicodedata
import random
from html import escape
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile, User as TgUser
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import BASE_DIR, Settings
from app.enums import ActionType, GamePhase, GameStatus, LogType, Role, Team
from app.keyboards import (
    confirm_hang_keyboard,
    commissar_action_keyboard,
    go_private_keyboard,
    go_role_private_keyboard,
    go_vote_private_keyboard,
    go_group_keyboard,
    group_url_from_chat_id,
    judge_cancel_keyboard,
    lobby_keyboard,
    miner_keyboard,
    profile_dashboard_keyboard,
    target_keyboard,
    vote_keyboard,
)
from app.models import (
    BotSetting,
    Game,
    GameLog,
    GamePlayer,
    Group,
    HangVote,
    NightAction,
    NightPrompt,
    PremiumGroup,
    PremiumBlockedUser,
    PremiumGroupContribution,
    SkipDecision,
    User,
    Vote,
)
from app.roles import ROLE_META, build_role_set, role_label, role_preset_label, role_preset_max_players, role_team
from app.scheduler import scheduler
from app.texts import t

logger = logging.getLogger(__name__)

INVISIBLE_NAME_CHARS = {
    "\u034f",
    "\u061c",
    "\u115f",
    "\u1160",
    "\u17b4",
    "\u17b5",
    "\u180e",
    "\u200b",
    "\u200c",
    "\u200d",
    "\u200e",
    "\u200f",
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
    "\u2060",
    "\u2061",
    "\u2062",
    "\u2063",
    "\u2064",
    "\u2066",
    "\u2067",
    "\u2068",
    "\u2069",
    "\u2800",
    "\u3164",
    "\ufeff",
}


class GameEngine:
    def __init__(self, settings: Settings, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self._group_language_cache: dict[int, tuple[float, str]] = {}
        self._group_return_url_cache: dict[int, tuple[float, str]] = {}
        self._active_participants_cache: dict[int, tuple[float, Optional[int], frozenset[int]]] = {}
        self._cache_ttl_seconds = 10.0
        self._return_url_cache_ttl_seconds = 3600.0
        self._cache_limit = 20000

    def _monotonic(self) -> float:
        return asyncio.get_running_loop().time()

    def _prune_cache_if_needed(self, cache: dict[int, tuple[float, object]]) -> None:
        if len(cache) < self._cache_limit:
            return
        now = self._monotonic()
        expired_keys = [key for key, value in cache.items() if value[0] <= now]
        for key in expired_keys:
            cache.pop(key, None)
        if len(cache) < self._cache_limit:
            return
        for key in list(cache)[: max(1, self._cache_limit // 10)]:
            cache.pop(key, None)

    def _invalidate_group_cache(self, chat_id: int) -> None:
        self._group_language_cache.pop(chat_id, None)
        self._group_return_url_cache.pop(chat_id, None)

    def _invalidate_game_cache(self, chat_id: int) -> None:
        self._active_participants_cache.pop(chat_id, None)

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _ensure_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _tg_mention(user_id: int, display_name: str) -> str:
        safe_name = escape(display_name or "Unknown")
        return f'<a href="tg://user?id={user_id}">{safe_name}</a>'

    @staticmethod
    def _display_name_from_tg(tg_user: TgUser) -> str:
        parts = [tg_user.first_name, tg_user.last_name]
        name = " ".join(part for part in parts if part).strip()
        if name and GameEngine._has_visible_nickname(name):
            return name[:255]
        if tg_user.username:
            return tg_user.username[:255]
        return f"Player {tg_user.id}"

    @staticmethod
    def _profile_name_from_tg(tg_user: TgUser) -> str:
        parts = [tg_user.first_name, tg_user.last_name]
        return " ".join(part for part in parts if part).strip()

    @staticmethod
    def _has_visible_nickname(name: str) -> bool:
        for char in name:
            if char in INVISIBLE_NAME_CHARS or char.isspace():
                continue
            category = unicodedata.category(char)
            if category[0] in {"L", "N", "P", "S"}:
                return True
        return False

    def _format_alive_players(self, players: list[GamePlayer]) -> str:
        if not players:
            return "-"
        return "\n".join(
            f"{idx}. {self._tg_mention(player.telegram_id, player.display_name)}"
            for idx, player in enumerate(players, 1)
        )

    @staticmethod
    def _format_role_group(title: str, count: int, players: list[GamePlayer]) -> str:
        if count == 0:
            return ""
        role_counter = Counter(player.role for player in players)
        role_lines = []
        for role_value, role_count in role_counter.items():
            suffix = f" - {role_count}" if role_count > 1 else ""
            role_lines.append(f"{role_label(role_value)}{suffix}")
        return f"{title} - <b>{count}</b>\n{', '.join(role_lines)}"

    def _format_death_line(self, player: GamePlayer, cause: Optional[str] = None) -> str:
        name = self._tg_mention(player.telegram_id, player.display_name)
        base = f"Tunda {role_label(player.role)} {name}"
        if cause == "mafia":
            return f"{base} Mafiya tomonidan vaxshiylarcha o'ldirildi..."
        if cause == "killer":
            return f"{base} Qotil tomonidan vaxshiylarcha o'ldirildi..."
        if cause == "commissar":
            return f"{base} Komissar Katani o'qidan halok bo'ldi..."
        if cause == "sorcerer":
            return f"{base} Afsungar qasosi bilan o'ldirildi..."
        if cause == "miner":
            return f"{base} o'lim koniga qulab tushdi..."
        return f"{base} vaxshiylarcha o'ldirildi..."

    def _death_story_line(
        self,
        player: GamePlayer,
        cause: Optional[str] = None,
        visitor_label: Optional[str] = None,
    ) -> str:
        name = self._tg_mention(player.telegram_id, player.display_name)
        role = role_label(player.role)
        if visitor_label:
            visitor = visitor_label
        elif cause == "mafia":
            visitor = "🤵🏻 Don yoki Mafiya"
        elif cause == "killer":
            visitor = "🔪 Qotil"
        elif cause == "commissar":
            visitor = "🕵🏼 Komissar Katani"
        elif cause == "sorcerer":
            visitor = "🧙‍ Sehrgar qasosi"
        elif cause == "miner":
            visitor = "👷 o'lim koni"
        else:
            visitor = "noma'lum mehmon"
        return f"Tunda {role} {name} vaxshiylarcha o'ldirildi. Aytishlaricha unikiga {visitor} kelgan."

    def _build_alive_status_text(self, alive_players: list[GamePlayer]) -> str:
        city_players = [player for player in alive_players if player.team == Team.CITY.value]
        mafia_players = [player for player in alive_players if player.team == Team.MAFIA.value]
        singleton_players = [
            player
            for player in alive_players
            if player.team in {Team.KILLER.value, Team.NEUTRAL.value}
        ]

        group_blocks = [
            self._format_role_group("🤵🏻 <b>Mafiya</b>", len(mafia_players), mafia_players),
            self._format_role_group("👨🏼 <b>Singleton</b>", len(singleton_players), singleton_players),
            self._format_role_group("🏘 <b>Tinch aholilar</b>", len(city_players), city_players),
        ]
        groups_text = "\n\n".join(block for block in group_blocks if block)
        return (
            "<b>Tirik o'yinchilar:</b>\n"
            f"{self._format_alive_players(alive_players)}\n\n"
            f"{groups_text}\n\n"
            f"<b>Jami:</b> {len(alive_players)}"
        )

    @staticmethod
    def _build_day_intro_text(day_number: int) -> str:
        return (
            "Xayrli tong🌝 \n"
            f"🌄<b>Kun: {day_number}</b>\n"
            "Shamollar tundagi mish-mishlarni butun shaharga yetkazmoqda..\n\n"
            "Endi kechaning natijalarini muhokama qilish, sabablari va oqibatlarini tushunish vaqti keldi ..."
        )

    def _build_night_story_messages(
        self,
        dead_players: list[GamePlayer],
        transformed: list[str],
        night_activity_lines: list[str],
        night_event_lines: list[str],
        death_causes: Optional[dict[int, str]] = None,
        death_visitors: Optional[dict[int, str]] = None,
    ) -> list[str]:
        death_causes = death_causes or {}
        death_visitors = death_visitors or {}
        messages: list[str] = []

        def add_once(line: str) -> None:
            clean = line.strip()
            if clean and clean not in messages:
                messages.append(clean)

        for line in night_activity_lines:
            add_once(line)
        if dead_players:
            for player in dead_players:
                add_once(
                    self._death_story_line(
                        player,
                        death_causes.get(player.telegram_id),
                        death_visitors.get(player.telegram_id),
                    )
                )
        else:
            add_once("Ishonish qiyin, lekin bu tunda hech kim o'lmadi...")

        for line in transformed:
            add_once(f"🔁 {line}")
        return messages

    @staticmethod
    def _private_role_text(role: Role) -> str:
        meta = ROLE_META[role]
        return f"Siz - {meta.emoji} <b>{meta.title_uz}</b>siz!\n{meta.short_desc_uz}"

    def _commissar_check_result_text(self, target: GamePlayer, seen_role: Role) -> str:
        return f"{self._tg_mention(target.telegram_id, target.display_name)} - {role_label(seen_role)}"

    @staticmethod
    def _night_activity_line(role: Role, action_key: Optional[str]) -> Optional[str]:
        if role == Role.DOCTOR:
            return "👨🏼‍⚕️️Doktor tungi navbatchilikga ketdi..."
        if role == Role.GUARD:
            return "🛡 Qo'riqchi tun bo'yi bir odamni himoya qilishga ketdi..."
        if role == Role.WATCHER:
            return "🔎 Kuzatuvchi qorong'ida izlarni sanadi..."
        if role == Role.MISTRESS:
            return "💃 Kezuvchining qandaydir mehmoni bor ekan..."
        if role == Role.DON:
            return "🤵🏻 Don navbatdagi o'ljasini tanladi..."
        if role == Role.MAFIA:
            return "🤵🏼 Mafiya bugungi o'ljasini tanladi..."
        if role == Role.SPY:
            return "🕴 Josus qorong'ida iz qoldirmay harakat qildi..."
        if role == Role.JOURNALIST:
            return "👩🏼‍💻 Jurnalist intervyu olish uchun ketti..."
        if role == Role.HIRED_KILLER:
            return "🥷 Yollanma qotil o'ljasini tanladi..."
        if role == Role.COMMISSAR:
            if action_key == "shoot":
                return "🕵🏼 Komissar katani tungi vazifasiga ketdi..."
            return "🕵🏼 Komissar Katani yovuzlarni qidirishga ketdi..."
        if role == Role.LAWYER:
            return "👨🏼‍💼 Advokat Mafiani ximoya qilish uchun qidiryapti..."
        if role == Role.KILLER:
            return "🔪 Qotil navbatdagi qurbonini tanladi..."
        if role == Role.BUM:
            return "🧙🏼 Daydi kimnikigadir ichkilik butilka olish uchun ketdi..."
        if role == Role.CROOK:
            return "🤹🏻 Aferist o'ljasini tanladi."
        if role == Role.MINER:
            if action_key == "mine_protect":
                return "👷 Konchi o'zini himoyalashga qaror qildi..."
            return "👷 Konchi konlardan biriga yo'l oldi..."
        return None

    def _last_words_line(self, player: GamePlayer, words: str) -> str:
        safe_words = escape(words.strip()[:500])
        name = self._tg_mention(player.telegram_id, player.display_name)
        return f"O'limidan oldin {name} qichqirganini eshitdi:\n{safe_words}"

    def _sleep_death_line(self, player: GamePlayer) -> str:
        name = self._tg_mention(player.telegram_id, player.display_name)
        role = role_label(player.role)
        return (
            f"Aholidan kimdir {role} {name} o'limidan oldin:\n"
            '"Men o\'yin paytida boshqa uxlamayma-a-a-a-a-a-an!" - deb qichqirganini eshitgan.'
        )

    def _apply_role_successions(self, alive_players: list[GamePlayer], dead_ids: set[int]) -> list[str]:
        lines: list[str] = []
        dead_players = [player for player in alive_players if player.telegram_id in dead_ids]

        if any(Role(player.role) == Role.DON for player in dead_players):
            heir = next(
                (
                    player
                    for player in alive_players
                    if player.telegram_id not in dead_ids
                    and Role(player.role) in {Role.MAFIA, Role.SPY, Role.JOURNALIST, Role.HIRED_KILLER}
                ),
                None,
            )
            if heir:
                heir.role = Role.DON.value
                heir.team = Team.MAFIA.value
                lines.append(
                    f"🤵🏻 Donning qora merosi {self._tg_mention(heir.telegram_id, heir.display_name)} qo'liga o'tdi."
                )

        if any(Role(player.role) == Role.COMMISSAR for player in dead_players):
            heir = next(
                (
                    player
                    for player in alive_players
                    if player.telegram_id not in dead_ids and Role(player.role) == Role.SERGEANT
                ),
                None,
            )
            if heir:
                heir.role = Role.COMMISSAR.value
                heir.team = Team.CITY.value
                lines.append(
                    f"👮🏻‍♂ Serjant {self._tg_mention(heir.telegram_id, heir.display_name)} Komissar Katani vazifasini davom ettiradi."
                )

        return lines

    async def ensure_user(self, tg_user: TgUser, language: Optional[str] = None) -> User:
        async with self.session_factory() as session:
            stmt = select(User).where(User.telegram_id == tg_user.id)
            user = (await session.execute(stmt)).scalar_one_or_none()
            full_name = self._display_name_from_tg(tg_user)
            if user is None:
                user = User(
                    telegram_id=tg_user.id,
                    username=tg_user.username,
                    display_name=full_name,
                    language=language or self.settings.default_language,
                    language_selected=bool(language),
                    diamonds=0,
                    dollar=0,
                )
                session.add(user)
            else:
                user.username = tg_user.username
                user.display_name = full_name
                if language:
                    user.language = language
                    user.language_selected = True
            await session.commit()
            await session.refresh(user)
            return user

    async def get_user(self, telegram_id: int) -> Optional[User]:
        async with self.session_factory() as session:
            return (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()

    async def get_or_create_group(self, chat_id: int, title: str) -> Group:
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                group = Group(
                    chat_id=chat_id,
                    title=title,
                    language=self.settings.default_language,
                    registration_timeout=self.settings.registration_timeout,
                    min_players=self.settings.min_players,
                )
                session.add(group)
            else:
                group.title = title
            await session.commit()
            await session.refresh(group)
            self._invalidate_group_cache(chat_id)
            return group

    async def set_user_language(self, telegram_id: int, language: str) -> None:
        async with self.session_factory() as session:
            user = (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
            if user is None:
                user = User(
                    telegram_id=telegram_id,
                    display_name="Unknown",
                    language=language,
                    language_selected=True,
                )
                session.add(user)
            else:
                user.language = language
                user.language_selected = True
            await session.commit()

    async def set_group_language(self, chat_id: int, language: str) -> None:
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                group = Group(chat_id=chat_id, title="Group", language=language)
                session.add(group)
            else:
                group.language = language
            await session.commit()
        self._invalidate_group_cache(chat_id)

    async def get_user_language(self, telegram_id: int) -> str:
        user = await self.get_user(telegram_id)
        if user and user.language:
            return user.language
        return self.settings.default_language

    async def get_group_language(self, chat_id: int) -> str:
        now = self._monotonic()
        cached = self._group_language_cache.get(chat_id)
        if cached and cached[0] > now:
            return cached[1]
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            language = group.language if group else self.settings.default_language
        self._prune_cache_if_needed(self._group_language_cache)
        self._group_language_cache[chat_id] = (now + self._cache_ttl_seconds, language)
        return language

    async def group_return_url(self, bot: Bot, chat_id: int) -> str:
        now = self._monotonic()
        cached = self._group_return_url_cache.get(chat_id)
        if cached and cached[0] > now:
            return cached[1]

        url = group_url_from_chat_id(chat_id)
        try:
            chat = await bot.get_chat(chat_id)
            username = getattr(chat, "username", None)
            if username:
                url = f"https://t.me/{username}"
                self._prune_cache_if_needed(self._group_return_url_cache)
                self._group_return_url_cache[chat_id] = (now + self._return_url_cache_ttl_seconds, url)
                return url

            invite_link = getattr(chat, "invite_link", None)
            if invite_link:
                url = invite_link
                self._prune_cache_if_needed(self._group_return_url_cache)
                self._group_return_url_cache[chat_id] = (now + self._return_url_cache_ttl_seconds, url)
                return url

            try:
                url = await bot.export_chat_invite_link(chat_id)
            except (TelegramBadRequest, TelegramForbiddenError):
                pass
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

        self._prune_cache_if_needed(self._group_return_url_cache)
        self._group_return_url_cache[chat_id] = (now + self._return_url_cache_ttl_seconds, url)
        return url

    async def group_return_keyboard(self, bot: Bot, chat_id: int):
        return go_group_keyboard(chat_id, await self.group_return_url(bot, chat_id))

    async def log(self, game_id: int, event_type: str, payload: str) -> None:
        async with self.session_factory() as session:
            session.add(GameLog(game_id=game_id, event_type=event_type, payload=payload))
            await session.commit()

    @staticmethod
    def _player_log_snapshot(player: Optional[GamePlayer]) -> Optional[dict[str, object]]:
        if player is None:
            return None
        return {
            "telegram_id": player.telegram_id,
            "display_name": player.display_name,
            "role": player.role,
            "team": player.team,
            "alive": player.alive,
        }

    def _build_log_payload(
        self,
        game: Game,
        *,
        actor: Optional[GamePlayer] = None,
        target: Optional[GamePlayer] = None,
        **metadata: object,
    ) -> str:
        payload = {
            "chat_id": game.chat_id,
            "status": game.status,
            "phase": game.phase,
            "day_number": game.day_number,
            "night_number": game.night_number,
            "actor": self._player_log_snapshot(actor),
            "target": self._player_log_snapshot(target),
            "metadata": metadata,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _add_game_log(
        self,
        session: AsyncSession,
        game: Game,
        event_type: str,
        *,
        actor: Optional[GamePlayer] = None,
        target: Optional[GamePlayer] = None,
        **metadata: object,
    ) -> None:
        session.add(
            GameLog(
                game_id=game.id,
                event_type=event_type,
                payload=self._build_log_payload(game, actor=actor, target=target, **metadata),
            )
        )

    async def find_active_game(self, session: AsyncSession, chat_id: int) -> Optional[Game]:
        stmt = select(Game).where(
            Game.chat_id == chat_id,
            Game.status.in_([GameStatus.REGISTRATION.value, GameStatus.ACTIVE.value]),
        ).order_by(Game.id.desc())
        return (await session.execute(stmt)).scalars().first()

    async def create_game_registration(self, bot: Bot, chat_id: int, chat_title: str, creator_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            active = await self.find_active_game(session, chat_id)
            lang = await self.get_group_language(chat_id)
            if active is not None:
                return False, t(lang, "active_game_exists")

            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                group = Group(
                    chat_id=chat_id,
                    title=chat_title,
                    language=self.settings.default_language,
                    registration_timeout=self.settings.registration_timeout,
                    min_players=self.settings.min_players,
                )
                session.add(group)
                await session.flush()
            else:
                group.title = chat_title

            timeout = max(30, group.registration_timeout or self.settings.registration_timeout)
            ends_at = self._now_utc() + timedelta(seconds=timeout)

            game = Game(
                chat_id=chat_id,
                creator_telegram_id=creator_id,
                status=GameStatus.REGISTRATION.value,
                phase=GamePhase.REGISTRATION.value,
                active_key=1,
                registration_ends_at=ends_at,
            )
            session.add(game)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                return False, t(lang, "active_game_exists")

            text = await self._build_lobby_text(session, game.id, lang, ended=False)
            msg = await bot.send_message(
                chat_id,
                text,
                reply_markup=lobby_keyboard(
                    lang=lang,
                    game_id=game.id,
                    bot_username=self.settings.bot_username,
                    chat_id=chat_id,
                    active=True,
                ),
            )
            game.lobby_message_id = msg.message_id
            self._add_game_log(
                session,
                game,
                "registration_started",
                creator_id=creator_id,
                registration_timeout=timeout,
                registration_ends_at=ends_at.isoformat(),
            )
            await session.commit()

        self._invalidate_group_cache(chat_id)
        self._invalidate_game_cache(chat_id)
        try:
            await bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
        except TelegramBadRequest:
            await bot.send_message(chat_id, "⚠️ Pin qilishga ruxsat yo'q, lekin o'yin davom etadi.")

        try:
            await self.schedule_registration_jobs(bot, game.id)
        except Exception:
            logger.exception("Failed to schedule registration jobs for game_id=%s", game.id)
        return True, t(await self.get_group_language(chat_id), "registration_started")

    async def _build_lobby_text(self, session: AsyncSession, game_id: int, lang: str, ended: bool) -> str:
        players = (
            await session.execute(
                select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.id.asc())
            )
        ).scalars().all()
        if ended:
            title = t(lang, "lobby_ended_title")
        elif players:
            title = t(lang, "lobby_title")
        else:
            return t(lang, "lobby_started_title")

        names = ", ".join(self._tg_mention(p.telegram_id, p.display_name) for p in players) if players else t(lang, "lobby_empty")
        return (
            f"{title}\n"
            f"{t(lang, 'lobby_registered')}\n\n"
            f"{names}\n\n"
            f"{t(lang, 'lobby_total', count=len(players))}"
        )

    async def update_lobby(self, bot: Bot, game_id: int, ended: bool = False) -> None:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.lobby_message_id is None:
                return
            lang = await self.get_group_language(game.chat_id)
            text = await self._build_lobby_text(session, game.id, lang, ended)
            kb = lobby_keyboard(
                lang=lang,
                game_id=game.id,
                bot_username=self.settings.bot_username,
                chat_id=game.chat_id,
                active=(not ended),
            )

        try:
            await bot.edit_message_text(
                chat_id=game.chat_id,
                message_id=game.lobby_message_id,
                text=text,
                reply_markup=kb,
            )
        except TelegramBadRequest:
            logger.warning("Unable to update lobby message for game %s", game_id)

    async def schedule_registration_jobs(self, bot: Bot, game_id: int) -> None:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.registration_ends_at is None:
                return
            ends_at = self._ensure_utc(game.registration_ends_at)

        now = self._now_utc()
        seconds_left = int((ends_at - now).total_seconds())
        if seconds_left <= 0:
            await self.close_registration(bot, game_id)
            return

        if seconds_left > 60:
            scheduler.add_job(
                self.send_registration_warning,
                "date",
                run_date=ends_at - timedelta(seconds=60),
                args=[bot, game_id, 60],
                id=f"reg_warn_60_{game_id}",
                replace_existing=True,
                misfire_grace_time=30,
            )

        if seconds_left > 30:
            scheduler.add_job(
                self.send_registration_warning,
                "date",
                run_date=ends_at - timedelta(seconds=30),
                args=[bot, game_id, 30],
                id=f"reg_warn_30_{game_id}",
                replace_existing=True,
                misfire_grace_time=30,
            )

        scheduler.add_job(
            self.close_registration,
            "date",
            run_date=ends_at,
            args=[bot, game_id],
            id=f"reg_close_{game_id}",
            replace_existing=True,
            misfire_grace_time=120,
        )

    async def send_registration_warning(self, bot: Bot, game_id: int, seconds_left: int) -> None:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.REGISTRATION.value:
                return
            lang = await self.get_group_language(game.chat_id)
            key = "timer_60" if seconds_left == 60 else "timer_30"
        await self.update_lobby(bot, game_id)
        await self.log(game_id, LogType.GAME_EVENT.value, f"Registration warning {seconds_left}s")
        await bot.send_message(game.chat_id, t(lang, key))

    async def join_game(self, bot: Bot, game_id: int, tg_user: TgUser) -> tuple[bool, str]:
        if not self._has_visible_nickname(self._profile_name_from_tg(tg_user)):
            return (
                False,
                "Nikingiz ko'rinmayapti. O'yinda qatnashish uchun Telegram ismingizni ko'rinadigan qilib o'zgartiring va qayta urinib ko'ring.",
            )
        user = await self.ensure_user(tg_user)
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None:
                return False, t(self.settings.default_language, "callback_expired")
            lang = await self.get_group_language(game.chat_id)
            if game.status != GameStatus.REGISTRATION.value:
                return False, t(lang, "registration_closed_cb")
            group = (await session.execute(select(Group).where(Group.chat_id == game.chat_id))).scalar_one_or_none()
            preset = group.role_preset if group else "black23"
            max_players = role_preset_max_players(preset)
            current_count = await session.scalar(select(func.count(GamePlayer.id)).where(GamePlayer.game_id == game_id))
            if (current_count or 0) >= max_players:
                return False, f"Bu role preset uchun limit: {max_players} o'yinchi."

            exists = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == tg_user.id,
                    )
                )
            ).scalar_one_or_none()
            if exists is not None:
                return False, t(lang, "already_joined")

            player = GamePlayer(
                game_id=game_id,
                user_id=user.id,
                telegram_id=tg_user.id,
                display_name=user.display_name,
            )
            session.add(player)
            try:
                await session.flush()
                self._add_game_log(session, game, "player_joined", actor=player)
                chat_id = game.chat_id
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False, t(lang, "already_joined")

        self._invalidate_game_cache(chat_id)
        await self.update_lobby(bot, game_id)
        return True, t(await self.get_user_language(tg_user.id), "joined")

    async def join_game_by_deeplink(
        self,
        bot: Bot,
        game_id: int,
        chat_id: int,
        tg_user: TgUser,
    ) -> tuple[bool, str]:
        if not self._has_visible_nickname(self._profile_name_from_tg(tg_user)):
            return (
                False,
                "Nikingiz ko'rinmayapti. O'yinda qatnashish uchun Telegram ismingizni ko'rinadigan qilib o'zgartiring va qayta urinib ko'ring.",
            )
        await self.ensure_user(tg_user)
        async with self.session_factory() as session:
            game = (
                await session.execute(
                    select(Game).where(Game.id == game_id, Game.chat_id == chat_id)
                )
            ).scalar_one_or_none()
            lang = await self.get_group_language(chat_id)
            if game is None:
                return False, t(lang, "callback_expired")
            if game.status != GameStatus.REGISTRATION.value:
                return False, t(lang, "registration_closed_cb")

        return await self.join_game(bot=bot, game_id=game_id, tg_user=tg_user)

    async def leave_game(self, bot: Bot, game_id: int, tg_user_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None:
                return False, t(self.settings.default_language, "no_active_game")
            lang = await self.get_group_language(game.chat_id)

            if game.status != GameStatus.REGISTRATION.value:
                return False, t(lang, "cannot_leave_running")

            player = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == tg_user_id,
                    )
                )
            ).scalar_one_or_none()
            if player is None:
                return False, t(lang, "not_joined")
            self._add_game_log(session, game, "player_left", actor=player)
            chat_id = game.chat_id
            await session.delete(player)
            await session.commit()

        self._invalidate_game_cache(chat_id)
        await self.update_lobby(bot, game_id)
        return True, t(lang, "left_game")

    async def extend_registration(self, bot: Bot, game_id: int, seconds: int) -> tuple[bool, str]:
        seconds = 60 if seconds >= 60 else 30
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None:
                return False, t(self.settings.default_language, "no_active_game")
            lang = await self.get_group_language(game.chat_id)
            if game.status != GameStatus.REGISTRATION.value:
                return False, t(lang, "registration_closed_cb")
            current_end = self._ensure_utc(game.registration_ends_at) if game.registration_ends_at else self._now_utc()
            if current_end < self._now_utc():
                current_end = self._now_utc()
            game.registration_ends_at = current_end + timedelta(seconds=seconds)
            self._add_game_log(
                session,
                game,
                "registration_extended",
                seconds=seconds,
                registration_ends_at=game.registration_ends_at.isoformat(),
            )
            await session.commit()

        try:
            await self.schedule_registration_jobs(bot, game_id)
        except Exception:
            logger.exception("Failed to reschedule registration jobs for game_id=%s", game_id)
        return True, t(await self.get_group_language(game.chat_id), "extended")

    async def stop_game(self, bot: Bot, game_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None:
                return False, t(self.settings.default_language, "no_active_game")
            game.status = GameStatus.CANCELLED.value
            game.phase = GamePhase.ENDED.value
            game.active_key = None
            game.ended_at = datetime.now(timezone.utc)
            self._add_game_log(session, game, "game_cancelled", reason="manual_stop")
            await session.commit()
            chat_id = game.chat_id
            lang = await self.get_group_language(game.chat_id)

        self._invalidate_game_cache(chat_id)
        await self.update_lobby(bot, game_id, ended=True)
        self._cleanup_jobs(game_id)
        return True, t(lang, "game_cancelled")

    def _cleanup_jobs(self, game_id: int) -> None:
        for prefix in [
            "reg_warn_60_",
            "reg_warn_30_",
            "reg_close_",
            "night_end_",
            "discussion_end_",
            "vote_end_",
            "hang_confirm_",
        ]:
            job_id = f"{prefix}{game_id}"
            job = scheduler.get_job(job_id)
            if job:
                job.remove()

    async def close_registration(self, bot: Bot, game_id: int) -> None:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.REGISTRATION.value:
                return

            group = (await session.execute(select(Group).where(Group.chat_id == game.chat_id))).scalar_one_or_none()
            min_players = group.min_players if group else self.settings.min_players
            players = (
                await session.execute(select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.id.asc()))
            ).scalars().all()
            lang = await self.get_group_language(game.chat_id)

            if len(players) < min_players:
                game.status = GameStatus.CANCELLED.value
                game.phase = GamePhase.ENDED.value
                game.active_key = None
                game.ended_at = datetime.now(timezone.utc)
                self._add_game_log(
                    session,
                    game,
                    "game_cancelled",
                    reason="insufficient_players",
                    players_count=len(players),
                    min_players=min_players,
                )
                await session.commit()
                self._invalidate_game_cache(game.chat_id)
                await self.update_lobby(bot, game_id, ended=True)
                await bot.send_message(game.chat_id, t(lang, "insufficient_players"))
                self._cleanup_jobs(game_id)
                return

            game.status = GameStatus.ACTIVE.value
            game.phase = GamePhase.NIGHT.value
            game.started_at = datetime.now(timezone.utc)
            game.night_number = 1
            self._add_game_log(
                session,
                game,
                "registration_closed",
                players_count=len(players),
                min_players=min_players,
            )
            await session.commit()
            self._invalidate_game_cache(game.chat_id)

        await self.update_lobby(bot, game_id, ended=True)
        chat_id = await self._game_chat_id(game_id)
        await bot.send_message(chat_id, t(lang, "registration_ended"))
        await self.assign_roles_and_notify(bot, game_id)
        await bot.send_message(
            chat_id,
            "<b>O'yin boshlandi!</b>",
            reply_markup=go_role_private_keyboard(self.settings, game_id),
        )
        await self.start_night(bot, game_id)

    async def _game_chat_id(self, game_id: int) -> int:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one()
            return game.chat_id

    async def assign_roles_and_notify(self, bot: Bot, game_id: int) -> None:
        async with self.session_factory() as session:
            players = (
                await session.execute(select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.id.asc()))
            ).scalars().all()
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one()
            group = (await session.execute(select(Group).where(Group.chat_id == game.chat_id))).scalar_one_or_none()
            role_preset = group.role_preset if group else "black23"
            roles = build_role_set(len(players), role_preset)
            users = {
                user.telegram_id: user
                for user in (
                    await session.execute(select(User).where(User.telegram_id.in_([p.telegram_id for p in players])))
                ).scalars().all()
            }
            assigned = dict(zip([p.telegram_id for p in players], roles))

            for player in players:
                user = users.get(player.telegram_id)
                if user is None or not user.next_game_role:
                    continue
                try:
                    desired = Role(user.next_game_role)
                except ValueError:
                    user.next_game_role = None
                    continue
                holder = next((p for p in players if assigned[p.telegram_id] == desired), None)
                if holder is None:
                    continue
                assigned[holder.telegram_id], assigned[player.telegram_id] = assigned[player.telegram_id], desired
                user.next_game_role = None

            for player, role in zip(players, roles):
                final_role = assigned[player.telegram_id]
                player.role = final_role.value
                player.team = role_team(final_role).value
                self._add_game_log(
                    session,
                    game,
                    "role_assigned",
                    actor=player,
                    role=final_role.value,
                    team=player.team,
                )
            await session.commit()

            lang = await self.get_group_language(game.chat_id)

        for player in players:
            role = Role(player.role)
            try:
                await bot.send_message(
                    player.telegram_id,
                    self._private_role_text(role),
                    reply_markup=await self.group_return_keyboard(bot, game.chat_id),
                )
            except TelegramForbiddenError:
                await bot.send_message(game.chat_id, f"{player.display_name}: {t(lang, 'need_start_for_role')}")

        # Send team messages for Mafia
        mafia_team = [player for player in players if player.team == Team.MAFIA.value]
        if mafia_team:
            mafia_lines = [
                f"{idx}. {role_label(player.role)} - {self._tg_mention(player.telegram_id, player.display_name)}"
                for idx, player in enumerate(mafia_team, 1)
            ]
            mafia_text = "<b>Mafia jamoasi:</b>\n" + "\n".join(mafia_lines)
            for player in mafia_team:
                try:
                    await bot.send_message(
                        player.telegram_id,
                        mafia_text,
                        reply_markup=await self.group_return_keyboard(bot, game.chat_id),
                    )
                except TelegramForbiddenError:
                    pass

        # Send team messages for Doctors
        doctors = [player for player in players if Role(player.role) == Role.DOCTOR]
        if doctors and len(doctors) > 1:
            doctor_lines = [
                f"{idx}. {self._tg_mention(player.telegram_id, player.display_name)}"
                for idx, player in enumerate(doctors, 1)
            ]
            doctor_text = "<b>👨🏼‍⚕️ Doktor jamoasi:</b>\n" + "\n".join(doctor_lines)
            for player in doctors:
                try:
                    await bot.send_message(
                        player.telegram_id,
                        doctor_text,
                        reply_markup=await self.group_return_keyboard(bot, game.chat_id),
                    )
                except TelegramForbiddenError:
                    pass

        # Send team messages for Commissar and Sergeant
        commissars = [player for player in players if Role(player.role) == Role.COMMISSAR]
        sergeants = [player for player in players if Role(player.role) == Role.SERGEANT]
        if commissars and sergeants:
            commissar_text = "<b>🕵🏼 Komissar Katani va Serjantlar:</b>\n"
            lines = [
                f"🕵🏼 {self._tg_mention(c.telegram_id, c.display_name)}" for c in commissars
            ] + [
                f"👮🏼 {self._tg_mention(s.telegram_id, s.display_name)}" for s in sergeants
            ]
            commissar_text += "\n".join(lines)
            
            for player in commissars + sergeants:
                try:
                    await bot.send_message(
                        player.telegram_id,
                        commissar_text,
                        reply_markup=await self.group_return_keyboard(bot, game.chat_id),
                    )
                except TelegramForbiddenError:
                    pass

    def _night_prompt_for_player(
        self,
        game_id: int,
        night_number: int,
        player: GamePlayer,
        alive_players: list[GamePlayer],
        miner_visits: Optional[dict[int, set[int]]] = None,
    ) -> Optional[tuple[str, object]]:
        role = Role(player.role)
        all_choices = [(p.telegram_id, p.display_name) for p in alive_players]
        targets = [(tid, name) for tid, name in all_choices if tid != player.telegram_id]
        mafia_targets = [
            (p.telegram_id, p.display_name)
            for p in alive_players
            if p.telegram_id != player.telegram_id and p.team != Team.MAFIA.value
        ]

        if role in {Role.MAFIA, Role.DON, Role.SPY, Role.HIRED_KILLER}:
            return "🌚 Kimni yo'q qilamiz?", target_keyboard("kill", game_id, player.telegram_id, mafia_targets)
        if role == Role.DOCTOR:
            return (
                "👨🏼‍⚕️️ Kimni davolaymiz?",
                target_keyboard("heal", game_id, player.telegram_id, all_choices),
            )
        if role == Role.GUARD:
            return "🛡 Kimni tunda himoya qilasiz?", target_keyboard("guard", game_id, player.telegram_id, all_choices)
        if role == Role.WATCHER:
            return "🔎 Kimni kuzatasiz? Unga kim kelganini bilasiz.", target_keyboard("watch", game_id, player.telegram_id, targets)
        if role == Role.COMMISSAR:
            return (
                "🕵🏼 Komissar katani",
                commissar_action_keyboard(
                    game_id=game_id,
                    actor_id=player.telegram_id,
                    can_shoot=night_number >= 2,
                ),
            )
        if role == Role.MISTRESS:
            return "💃 Kimni harakatdan to'xtatasiz?", target_keyboard("block", game_id, player.telegram_id, targets)
        if role == Role.LAWYER:
            return "👨‍💼 Kimni himoya qilasiz?", target_keyboard("defend", game_id, player.telegram_id, targets)
        if role == Role.KILLER:
            return "🔪 Kimni o'ldirasiz?", target_keyboard("killer", game_id, player.telegram_id, targets)
        if role == Role.BUM:
            return "🧙‍♂ Kimni kuzatasiz?", target_keyboard("visit", game_id, player.telegram_id, targets)
        if role == Role.JOURNALIST:
            return "👩🏼‍💻 Kimdan intervyu olasiz?", target_keyboard("watch", game_id, player.telegram_id, targets)
        if role == Role.CROOK:
            return "🤹🏻 Kimni chalg'itasiz?", target_keyboard("block", game_id, player.telegram_id, targets)
        if role == Role.MINER:
            visited = miner_visits.get(player.telegram_id, set()) if miner_visits else set()
            return "Qaysi konga borasiz?", miner_keyboard(game_id, player.telegram_id, visited)
        return None

    async def send_private_role_menu(self, bot: Bot, game_id: int, telegram_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status not in {GameStatus.ACTIVE.value, GameStatus.COMPLETED.value}:
                return False, "Bu o'yin topilmadi yoki hali boshlanmagan."

            player = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == telegram_id,
                    )
                )
            ).scalar_one_or_none()
            if player is None or player.role is None:
                return False, "Siz bu o'yinda ro'yxatdan o'tmagansiz."

            alive = await self._alive_players(session, game_id)
            prompt = None
            if game.phase == GamePhase.NIGHT.value and player.alive:
                existing_action = (
                    await session.execute(
                        select(NightAction.id).where(
                            NightAction.game_id == game_id,
                            NightAction.night_number == game.night_number,
                            NightAction.actor_telegram_id == telegram_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing_action is None:
                    prompt = self._night_prompt_for_player(game_id, game.night_number, player, alive)
            is_night = game.phase == GamePhase.NIGHT.value
            is_alive = player.alive
            night_number = game.night_number

        if prompt:
            text, keyboard = prompt
            prompt_message = await bot.send_message(telegram_id, text, reply_markup=keyboard)
            await self._remember_night_prompt(
                game_id=game_id,
                night_number=night_number,
                user_telegram_id=telegram_id,
                message_id=prompt_message.message_id,
            )
        elif is_night and is_alive:
            await bot.send_message(
                telegram_id,
                "🌚 Bu tun uchun faol tanlov mavjud emas yoki tanlovingiz allaqachon qabul qilingan.",
            )
        else:
            await bot.send_message(
                telegram_id,
                "🎭 Rolingiz o'yin boshida bir marta yuborilgan. Hozir faol tanlov bosqichi emas.",
            )
        return True, "Bot private chatiga kerakli ma'lumot yuborildi."

    async def _send_phase_media(
        self,
        bot: Bot,
        chat_id: int,
        is_night: bool,
        lang: str,
        game_id: Optional[int] = None,
        caption_override: Optional[str] = None,
    ) -> None:
        caption = caption_override or (t(lang, "night_title") if is_night else t(lang, "day_title"))
        kb = go_role_private_keyboard(self.settings, game_id, "Bot-ga o'tish ↗") if game_id else go_private_keyboard(self.settings)
        file_id = self.settings.night_media_file_id if is_night else self.settings.day_media_file_id
        local_path_str = self.settings.night_media_local if is_night else self.settings.day_media_local

        if file_id:
            try:
                await bot.send_animation(chat_id=chat_id, animation=file_id, caption=caption, reply_markup=kb)
                return
            except TelegramBadRequest:
                try:
                    await bot.send_video(
                        chat_id=chat_id,
                        video=file_id,
                        caption=caption,
                        reply_markup=kb,
                        supports_streaming=True,
                    )
                    return
                except TelegramBadRequest:
                    logger.warning("Media file_id invalid, fallback to local: %s", file_id)

        local_path = Path(local_path_str)
        if not local_path.is_absolute():
            local_path = BASE_DIR / local_path
        if not local_path.exists():
            logger.warning("Media file not found: %s", local_path)
            await bot.send_message(chat_id=chat_id, text=caption, reply_markup=kb)
            return

        try:
            if local_path.suffix.lower() == ".mp4":
                await bot.send_video(
                    chat_id=chat_id,
                    video=FSInputFile(str(local_path)),
                    caption=caption,
                    reply_markup=kb,
                    supports_streaming=True,
                )
            else:
                await bot.send_animation(
                    chat_id=chat_id,
                    animation=FSInputFile(str(local_path)),
                    caption=caption,
                    reply_markup=kb,
                )
        except TelegramBadRequest:
            await bot.send_message(chat_id=chat_id, text=caption, reply_markup=kb)

    async def start_night(self, bot: Bot, game_id: int) -> None:
        winner = await self.check_winner(game_id)
        if winner:
            await self.finish_game(bot, game_id, winner)
            return

        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value:
                return
            game.phase = GamePhase.NIGHT.value
            chat_id = game.chat_id
            lang = await self.get_group_language(chat_id)
            alive_players = await self._alive_players(session, game_id)
            alive_status = self._build_alive_status_text(alive_players)
            self._add_game_log(
                session,
                game,
                "night_started",
                alive_count=len(alive_players),
            )
            await session.commit()

        await self._send_phase_media(
            bot,
            chat_id,
            is_night=True,
            lang=lang,
            game_id=game_id,
        )
        await bot.send_message(chat_id, alive_status)
        await self.send_night_prompts(bot, game_id)
        run_at = datetime.now(timezone.utc) + timedelta(seconds=self.settings.night_timeout)
        scheduler.add_job(
            self.resolve_night,
            "date",
            run_date=run_at,
            args=[bot, game_id],
            id=f"night_end_{game_id}",
            replace_existing=True,
        )

    async def _alive_players(self, session: AsyncSession, game_id: int) -> list[GamePlayer]:
        return (
            await session.execute(
                select(GamePlayer).where(GamePlayer.game_id == game_id, GamePlayer.alive.is_(True)).order_by(GamePlayer.id.asc())
            )
        ).scalars().all()

    async def _remember_night_prompt(
        self,
        game_id: int,
        night_number: int,
        user_telegram_id: int,
        message_id: int,
    ) -> None:
        async with self.session_factory() as session:
            session.add(
                NightPrompt(
                    game_id=game_id,
                    night_number=night_number,
                    user_telegram_id=user_telegram_id,
                    message_id=message_id,
                )
            )
            await session.commit()

    async def _clear_night_prompt_buttons(self, bot: Bot, game_id: int, night_number: int) -> None:
        async with self.session_factory() as session:
            prompts = (
                await session.execute(
                    select(NightPrompt).where(
                        NightPrompt.game_id == game_id,
                        NightPrompt.night_number == night_number,
                        NightPrompt.cleared.is_(False),
                    )
                )
            ).scalars().all()

        for prompt in prompts:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=prompt.user_telegram_id,
                    message_id=prompt.message_id,
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass

        if prompts:
            async with self.session_factory() as session:
                prompt_ids = [prompt.id for prompt in prompts]
                rows = (
                    await session.execute(select(NightPrompt).where(NightPrompt.id.in_(prompt_ids)))
                ).scalars().all()
                for row in rows:
                    row.cleared = True
                await session.commit()

    async def send_night_prompts(self, bot: Bot, game_id: int) -> None:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one()
            night = game.night_number
            alive = await self._alive_players(session, game_id)
            mine_rows = (
                await session.execute(
                    select(NightAction.actor_telegram_id, NightAction.target_telegram_id).where(
                        NightAction.game_id == game_id,
                        NightAction.action_type == ActionType.MINE.value,
                        NightAction.target_telegram_id.is_not(None),
                    )
                )
            ).all()
            miner_visits: dict[int, set[int]] = defaultdict(set)
            for actor_id, mine_number in mine_rows:
                if mine_number is not None:
                    miner_visits[actor_id].add(mine_number)

        for player in alive:
            prompt = self._night_prompt_for_player(game_id, night, player, alive, miner_visits)
            if prompt is None:
                continue
            text, keyboard = prompt

            try:
                prompt_message = await bot.send_message(player.telegram_id, text, reply_markup=keyboard)
                await self._remember_night_prompt(
                    game_id=game_id,
                    night_number=night,
                    user_telegram_id=player.telegram_id,
                    message_id=prompt_message.message_id,
                )
            except TelegramForbiddenError:
                chat_id = (await self._game_chat_id(game_id))
                await bot.send_message(chat_id, f"{player.display_name}: /start orqali botga kiring.")

    async def record_action(
        self,
        bot: Bot,
        game_id: int,
        actor_id: int,
        action_key: str,
        target_id: int,
    ) -> tuple[bool, str]:
        action_map = {
            "kill": ActionType.KILL,
            "heal": ActionType.HEAL,
            "check": ActionType.CHECK,
            "shoot": ActionType.SHOOT,
            "block": ActionType.BLOCK,
            "defend": ActionType.DEFEND,
            "guard": ActionType.GUARD,
            "watch": ActionType.WATCH,
            "killer": ActionType.KILL,
            "visit": ActionType.VISIT,
            "revenge": ActionType.REVENGE_PICK,
            "mine": ActionType.MINE,
            "mine_protect": ActionType.MINE_PROTECT,
        }
        action_type = action_map.get(action_key)
        if action_type is None:
            return False, "Unknown action"

        success_text = t(self.settings.default_language, "action_saved")
        chat_id: Optional[int] = None
        don_notice_ids: list[int] = []
        don_notice_text: Optional[str] = None
        group_activity_line: Optional[str] = None
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value or game.phase != GamePhase.NIGHT.value:
                return False, t(self.settings.default_language, "callback_expired")
            chat_id = game.chat_id

            actor = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == actor_id,
                    )
                )
            ).scalar_one_or_none()
            if actor is None or not actor.alive:
                return False, t(self.settings.default_language, "not_alive")
            actor_role = Role(actor.role)

            allowed_actions: dict[Role, set[str]] = {
                Role.DON: {"kill"},
                Role.MAFIA: {"kill"},
                Role.SPY: {"kill"},
                Role.HIRED_KILLER: {"kill"},
                Role.DOCTOR: {"heal"},
                Role.GUARD: {"guard"},
                Role.WATCHER: {"watch"},
                Role.JOURNALIST: {"watch"},
                Role.COMMISSAR: {"check", "shoot"},
                Role.MISTRESS: {"block"},
                Role.CROOK: {"block"},
                Role.LAWYER: {"defend"},
                Role.KILLER: {"killer"},
                Role.BUM: {"visit"},
                Role.SORCERER: {"revenge"},
                Role.MINER: {"mine", "mine_protect"},
            }
            role_allowed = allowed_actions.get(actor_role, set())
            if action_key not in role_allowed:
                return False, "Bu amal sizning rolingiz uchun mavjud emas."
            if action_type == ActionType.MINE and not 1 <= target_id <= 10:
                return False, "Kon noto'g'ri."
            if action_type == ActionType.MINE:
                already_visited_mine = (
                    await session.execute(
                        select(NightAction.id).where(
                            NightAction.game_id == game_id,
                            NightAction.actor_telegram_id == actor_id,
                            NightAction.action_type == ActionType.MINE.value,
                            NightAction.target_telegram_id == target_id,
                        )
                    )
                ).scalar_one_or_none()
                if already_visited_mine is not None:
                    return False, "Bu konga oldin tashrif buyurgansiz. Boshqa kon tanlang."
            if action_type == ActionType.HEAL and target_id == actor_id and actor.self_heal_used:
                return False, "Siz o'zingizni yana davolay olmaysiz."

            if action_type == ActionType.HEAL:
                already_healed_target = (
                    await session.execute(
                        select(NightAction.id).where(
                            NightAction.game_id == game_id,
                            NightAction.actor_telegram_id == actor_id,
                            NightAction.target_telegram_id == target_id,
                            NightAction.action_type == ActionType.HEAL.value,
                        )
                    )
                ).scalar_one_or_none()
                if already_healed_target is not None:
                    return False, "Kimni davolaymiz?"

            if action_type in {ActionType.MINE, ActionType.MINE_PROTECT}:
                target = actor
            else:
                target = (
                    await session.execute(
                        select(GamePlayer).where(
                            GamePlayer.game_id == game_id,
                            GamePlayer.telegram_id == target_id,
                        )
                    )
                ).scalar_one_or_none()
                if target is None or not target.alive:
                    return False, "Nishon noto'g'ri."
            if action_key == "kill" and actor.team == Team.MAFIA.value and target.team == Team.MAFIA.value:
                return False, "Mafiya o'z sherigiga zarar yetkaza olmaydi."
            if actor_role == Role.MISTRESS and Role(target.role) == Role.COMMISSAR:
                return False, "Kezuvchi Komissarni qasddan uxlatmasligi kerak."
            if actor_role == Role.COMMISSAR and action_key == "check":
                success_text = f"Siz {target.display_name}ning uyiga tekshiruvga borishni tanladingiz."
            elif actor_role == Role.COMMISSAR and action_key == "shoot":
                success_text = f"Siz {target.display_name}ni o'yindan chetlatishni tanladingiz."
            elif action_key == "kill" and actor_role in {Role.MAFIA, Role.SPY, Role.HIRED_KILLER}:
                success_text = f"Siz - {target.display_name} ni tanladingiz. Don qaror qilmasa, tanlovingiz ishlaydi."
            elif action_type == ActionType.MINE:
                success_text = f"Siz {target_id:02d}-konni tanladingiz."
            elif action_type == ActionType.MINE_PROTECT:
                success_text = "Siz himoyalanishni tanladingiz."
            else:
                success_text = f"Siz - {target.display_name} ni tanladingiz."
            group_activity_line = self._night_activity_line(actor_role, action_key)

            existing = (
                await session.execute(
                    select(NightAction).where(
                        NightAction.game_id == game_id,
                        NightAction.night_number == game.night_number,
                        NightAction.actor_telegram_id == actor_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return False, t(self.settings.default_language, "action_already")

            session.add(
                NightAction(
                    game_id=game_id,
                    night_number=game.night_number,
                    actor_telegram_id=actor_id,
                    target_telegram_id=target_id if action_type != ActionType.MINE_PROTECT else None,
                    action_type=action_type.value,
                    details=action_key,
                )
            )

            if action_type == ActionType.HEAL and actor.telegram_id == target_id:
                actor.self_heal_used = True

            self._add_game_log(
                session,
                game,
                "night_action_saved",
                actor=actor,
                target=target,
                action_type=action_type.value,
                action_key=action_key,
            )
            if action_key == "kill" and actor_role in {Role.MAFIA, Role.SPY, Role.HIRED_KILLER}:
                dons = (
                    await session.execute(
                        select(GamePlayer).where(
                            GamePlayer.game_id == game_id,
                            GamePlayer.alive.is_(True),
                            GamePlayer.role == Role.DON.value,
                            GamePlayer.telegram_id != actor_id,
                        )
                    )
                ).scalars().all()
                don_notice_ids = [don.telegram_id for don in dons]
                don_notice_text = f"{actor.display_name} -- {target.display_name} ga ovoz berdi"
                if don_notice_ids:
                    group_activity_line = None

            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False, t(self.settings.default_language, "action_already")

        if chat_id is not None:
            try:
                await bot.send_message(
                    actor_id,
                    success_text,
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass
            if group_activity_line:
                await bot.send_message(chat_id, group_activity_line)
            if don_notice_text:
                for don_id in don_notice_ids:
                    try:
                        await bot.send_message(
                            don_id,
                            don_notice_text,
                            reply_markup=await self.group_return_keyboard(bot, chat_id),
                        )
                    except TelegramForbiddenError:
                        pass
        return True, success_text

    async def commissar_targets_keyboard(
        self,
        game_id: int,
        actor_id: int,
        action_key: str,
    ) -> tuple[bool, str, Optional[object]]:
        if action_key not in {"check", "shoot"}:
            return False, "Noma'lum amal.", None
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value or game.phase != GamePhase.NIGHT.value:
                return False, t(self.settings.default_language, "callback_expired"), None
            actor = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == actor_id,
                        GamePlayer.alive.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if actor is None or Role(actor.role) != Role.COMMISSAR:
                return False, "Bu amal faqat Komissar Katani uchun.", None
            existing = (
                await session.execute(
                    select(NightAction.id).where(
                        NightAction.game_id == game_id,
                        NightAction.night_number == game.night_number,
                        NightAction.actor_telegram_id == actor_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return False, t(self.settings.default_language, "action_already"), None
            alive = await self._alive_players(session, game_id)

        choices = [(p.telegram_id, p.display_name) for p in alive if p.telegram_id != actor_id]
        title = "Tekshirish" if action_key == "check" else "Otish"
        return True, title, target_keyboard(action_key, game_id, actor_id, choices)

    async def skip_choice(
        self,
        bot: Bot,
        game_id: int,
        user_id: int,
        scope: str,
    ) -> tuple[bool, str]:
        if scope not in {"night", "vote", "hang", "judge"}:
            return False, "Noma'lum tanlov."

        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value:
                return False, t(self.settings.default_language, "callback_expired")

            player = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == user_id,
                        GamePlayer.alive.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if player is None:
                return False, t(self.settings.default_language, "not_alive")

            if scope == "night":
                if game.phase != GamePhase.NIGHT.value:
                    return False, t(self.settings.default_language, "callback_expired")
                existing = (
                    await session.execute(
                        select(NightAction.id).where(
                            NightAction.game_id == game_id,
                            NightAction.night_number == game.night_number,
                            NightAction.actor_telegram_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return False, t(self.settings.default_language, "action_already")
                session.add(
                    NightAction(
                        game_id=game_id,
                        night_number=game.night_number,
                        actor_telegram_id=user_id,
                        target_telegram_id=None,
                        action_type=ActionType.SKIP.value,
                        details="skip",
                    )
                )
            elif scope == "vote":
                if game.phase != GamePhase.DAY_VOTING.value:
                    return False, t(self.settings.default_language, "callback_expired")
                existing_vote = (
                    await session.execute(
                        select(Vote.id).where(
                            Vote.game_id == game_id,
                            Vote.day_number == game.day_number,
                            Vote.voter_telegram_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing_vote is not None:
                    return False, t(self.settings.default_language, "vote_already")
            elif scope == "hang":
                if game.phase != GamePhase.DAY_CONFIRM.value:
                    return False, t(self.settings.default_language, "callback_expired")
                existing_hang = (
                    await session.execute(
                        select(HangVote.id).where(
                            HangVote.game_id == game_id,
                            HangVote.day_number == game.day_number,
                            HangVote.voter_telegram_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing_hang is not None:
                    return False, "Siz allaqachon tanlov qilgansiz."
            elif scope == "judge":
                if game.phase != GamePhase.DAY_CONFIRM.value:
                    return False, t(self.settings.default_language, "callback_expired")
                if Role(player.role) != Role.JUDGE:
                    return False, "Bu tugma faqat Sudya uchun."

            existing_skip = (
                await session.execute(
                    select(SkipDecision.id).where(
                        SkipDecision.game_id == game_id,
                        SkipDecision.phase == scope,
                        SkipDecision.day_number == game.day_number,
                        SkipDecision.night_number == game.night_number,
                        SkipDecision.user_telegram_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if existing_skip is not None:
                return False, "Siz allaqachon o'tkazib yuborgansiz."

            session.add(
                SkipDecision(
                    game_id=game_id,
                    phase=scope,
                    day_number=game.day_number,
                    night_number=game.night_number,
                    user_telegram_id=user_id,
                )
            )
            self._add_game_log(session, game, f"{scope}_skipped", actor=player)
            await session.commit()
            chat_id = game.chat_id
            player_name = self._tg_mention(player.telegram_id, player.display_name)

        group_text = f"🚷 {player_name} hech narsa qilmaslikka qaror qildi"
        await bot.send_message(chat_id, group_text)
        try:
            await bot.send_message(
                user_id,
                "Siz hech narsa qilmaslikka qaror qildingiz.",
                reply_markup=await self.group_return_keyboard(bot, chat_id),
            )
        except TelegramForbiddenError:
            pass
        return True, "O'tkazib yuborildi."

    async def resolve_night(self, bot: Bot, game_id: int) -> None:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value:
                return
            if game.phase != GamePhase.NIGHT.value:
                return

            night = game.night_number
            alive_players = await self._alive_players(session, game_id)
            alive_ids = {p.telegram_id for p in alive_players}
            player_map = {p.telegram_id: p for p in alive_players}

            actions = (
                await session.execute(
                    select(NightAction).where(
                        NightAction.game_id == game_id,
                        NightAction.night_number == night,
                    )
                )
            ).scalars().all()

            blocked: set[int] = set()
            defended: set[int] = set()
            guarded: set[int] = set()
            healed: set[int] = set()
            doctor_help_targets: set[int] = set()
            visited: dict[int, int] = {}
            watched: dict[int, int] = {}
            visitors_by_target: dict[int, list[int]] = defaultdict(list)

            for act in actions:
                if act.actor_telegram_id not in alive_ids:
                    continue
                actor = player_map[act.actor_telegram_id]
                if not actor.alive:
                    continue
                if act.action_type == ActionType.BLOCK.value and act.target_telegram_id:
                    blocked.add(act.target_telegram_id)

            for act in actions:
                if act.actor_telegram_id in blocked:
                    continue
                if act.action_type == ActionType.HEAL.value and act.target_telegram_id:
                    healed.add(act.target_telegram_id)
                    doctor_help_targets.add(act.target_telegram_id)
                elif act.action_type == ActionType.DEFEND.value and act.target_telegram_id:
                    defended.add(act.target_telegram_id)
                elif act.action_type == ActionType.GUARD.value and act.target_telegram_id:
                    guarded.add(act.target_telegram_id)
                elif act.action_type == ActionType.WATCH.value and act.target_telegram_id:
                    watched[act.actor_telegram_id] = act.target_telegram_id
                elif act.action_type == ActionType.VISIT.value and act.target_telegram_id:
                    visited[act.actor_telegram_id] = act.target_telegram_id
                elif act.action_type == ActionType.MINE_PROTECT.value:
                    guarded.add(act.actor_telegram_id)

                if (
                    act.target_telegram_id
                    and act.actor_telegram_id != act.target_telegram_id
                    and act.action_type not in {ActionType.WATCH.value, ActionType.MINE.value}
                ):
                    visitors_by_target[act.target_telegram_id].append(act.actor_telegram_id)

            don_kills = []
            mafia_fallback_kills = []
            killer_kills = []
            commissar_shots = []
            checks = []
            mine_actions: list[tuple[int, int]] = []
            miner_protectors: set[int] = set()
            night_activity_lines: list[str] = []

            for act in actions:
                if act.actor_telegram_id in blocked:
                    continue
                actor = player_map.get(act.actor_telegram_id)
                if actor is None:
                    continue
                role = Role(actor.role)
                target_id = act.target_telegram_id
                if target_id is None:
                    continue

                if act.action_type == ActionType.KILL.value and role == Role.DON:
                    don_kills.append(target_id)
                elif act.action_type == ActionType.KILL.value and role in {Role.MAFIA, Role.SPY, Role.HIRED_KILLER}:
                    mafia_fallback_kills.append(target_id)
                elif act.action_type == ActionType.KILL.value and role == Role.KILLER:
                    killer_kills.append(target_id)
                elif act.action_type == ActionType.SHOOT.value and role == Role.COMMISSAR:
                    commissar_shots.append(target_id)
                elif act.action_type == ActionType.CHECK.value and role == Role.COMMISSAR:
                    checks.append((act.actor_telegram_id, target_id))
                elif act.action_type == ActionType.MINE.value and role == Role.MINER:
                    mine_actions.append((act.actor_telegram_id, target_id))
                elif act.action_type == ActionType.MINE_PROTECT.value and role == Role.MINER:
                    miner_protectors.add(act.actor_telegram_id)

            dead: set[int] = set()
            death_causes: dict[int, str] = {}
            mafia_dead: set[int] = set()
            death_visitors: dict[int, str] = {}
            transformed: list[str] = []
            night_event_lines: list[str] = []
            protected_notices: list[tuple[int, str]] = []
            protected_group_lines: list[str] = []
            last_words_prompts: list[tuple[int, str]] = []
            miner_result_notices: list[tuple[int, str]] = []

            if mine_actions or miner_protectors:
                miner_users = {
                    user.telegram_id: user
                    for user in (
                        await session.execute(
                            select(User).where(
                                User.telegram_id.in_(
                                    [actor_id for actor_id, _ in mine_actions] + list(miner_protectors)
                                )
                            )
                        )
                    ).scalars().all()
                }
                for actor_id in miner_protectors:
                    miner_result_notices.append((actor_id, "⚜️ Siz bu tunda himoyalandingiz va konga bormadingiz."))
                for actor_id, mine_number in mine_actions:
                    miner = player_map.get(actor_id)
                    if miner is None or not miner.alive:
                        continue
                    layout = ["death"] * 3 + ["diamond"] * 2 + ["dollar"] * 5
                    rng = random.Random(f"{game_id}:{night}:{actor_id}")
                    rng.shuffle(layout)
                    result = layout[mine_number - 1]
                    user = miner_users.get(actor_id)
                    if result == "diamond":
                        if user:
                            user.diamonds += 1
                        miner_result_notices.append((actor_id, f"👷 {mine_number:02d}-kondan 💎 1 almaz topdingiz."))
                    elif result == "dollar":
                        if user:
                            user.dollar += 50
                        miner_result_notices.append((actor_id, f"👷 {mine_number:02d}-kondan 💵 50 dollar topdingiz."))
                    elif user and user.use_miner_protection is not False and (user.miner_protection or 0) > 0:
                        user.miner_protection -= 1
                        miner_result_notices.append(
                            (actor_id, f"👷 {mine_number:02d}-o'lim koniga tushdingiz, lekin Konchi himoyasi sizni qutqardi.")
                        )
                        protected_group_lines.append("👷 Kimdir Konchi himoyasini ishlatdi.")
                    else:
                        dead.add(actor_id)
                        death_causes[actor_id] = "miner"
                        death_visitors[actor_id] = role_label(Role.MINER)
                        miner_result_notices.append((actor_id, f"👷 {mine_number:02d}-o'lim koniga tushdingiz."))

            active_mafia_roles = {Role.DON} if don_kills else {Role.MAFIA, Role.SPY, Role.HIRED_KILLER}
            active_mafia_kills = don_kills if don_kills else mafia_fallback_kills
            mafia_target = Counter(active_mafia_kills).most_common(1)
            if mafia_target:
                target = mafia_target[0][0]
                target_player = player_map.get(target)
                if target_player:
                    if Role(target_player.role) == Role.WOLF:
                        target_player.role = Role.MAFIA.value
                        target_player.team = Team.MAFIA.value
                        transformed.append(f"🐺 {target_player.display_name} mafiyaga aylandi")
                    elif target not in healed and target not in guarded:
                        dead.add(target)
                        death_causes[target] = "mafia"
                        killer_actor = next(
                            (
                                player_map.get(action.actor_telegram_id)
                                for action in actions
                                if action.action_type == ActionType.KILL.value
                                and action.target_telegram_id == target
                                and action.actor_telegram_id in player_map
                                and Role(player_map[action.actor_telegram_id].role) in active_mafia_roles
                            ),
                            None,
                        )
                        if killer_actor:
                            death_visitors[target] = role_label(killer_actor.role)
                        mafia_dead.add(target)

            killer_target = Counter(killer_kills).most_common(1)
            if killer_target:
                target = killer_target[0][0]
                target_player = player_map.get(target)
                if target_player:
                    if Role(target_player.role) == Role.WOLF:
                        dead.add(target)
                        death_causes[target] = "killer"
                    elif target not in healed and target not in guarded and target_player.alive:
                        if target_player.telegram_id in defended:
                            pass
                        else:
                            dead.add(target)
                            death_causes[target] = "killer"
                            death_visitors[target] = role_label(Role.KILLER.value)

            for target in commissar_shots:
                target_player = player_map.get(target)
                if target_player is None:
                    continue
                if Role(target_player.role) == Role.WOLF:
                    target_player.role = Role.SERGEANT.value
                    target_player.team = Team.CITY.value
                    transformed.append(f"🐺 {target_player.display_name} serjantga aylandi")
                else:
                    dead.add(target)
                    death_causes[target] = "commissar"
                    death_visitors[target] = role_label(Role.COMMISSAR.value)

            if dead:
                protected_users = {
                    user.telegram_id: user
                    for user in (
                        await session.execute(select(User).where(User.telegram_id.in_(list(dead))))
                    ).scalars().all()
                }
                for victim_id in list(dead):
                    user = protected_users.get(victim_id)
                    cause = death_causes.get(victim_id)
                    if user is None or cause is None:
                        continue
                    if cause == "killer" and user.use_killer_protection is not False and (user.killer_protection or 0) > 0:
                        user.killer_protection -= 1
                        dead.discard(victim_id)
                        death_causes.pop(victim_id, None)
                        death_visitors.pop(victim_id, None)
                        protected_notices.append((victim_id, "⛑ Qotildan himoya sizni qutqarib qoldi."))
                        protected_group_lines.append("⛑ Kimdir qotildan himoyasini ishlatdi.")
                        self._add_game_log(
                            session,
                            game,
                            "killer_protection_used",
                            target=player_map.get(victim_id),
                            remaining_killer_protection=user.killer_protection,
                        )
                    elif cause in {"mafia", "commissar"} and user.use_protection is not False and (user.protection or 0) > 0:
                        user.protection -= 1
                        dead.discard(victim_id)
                        mafia_dead.discard(victim_id)
                        death_causes.pop(victim_id, None)
                        death_visitors.pop(victim_id, None)
                        protected_notices.append((victim_id, "🛡 Himoya sizni qutqarib qoldi."))
                        if cause == "mafia":
                            protected_group_lines.append("🛡 Kimdir himoyasini ishlatdi.")
                        else:
                            player = player_map.get(victim_id)
                            if player:
                                protected_group_lines.append(
                                    f"🛡 {self._tg_mention(player.telegram_id, player.display_name)} o'z himoyasini ishlatdi."
                                )
                        self._add_game_log(
                            session,
                            game,
                            "protection_used",
                            target=player_map.get(victim_id),
                            cause=cause,
                            remaining_protection=user.protection,
                        )

            # Daydi witness hint.
            witness_lines = []
            killed_targets = dead.copy()
            for observer_id, visited_id in visited.items():
                if visited_id in killed_targets:
                    observer = player_map.get(observer_id)
                    victim = player_map.get(visited_id)
                    if observer and victim:
                        witness_lines.append((observer.telegram_id, victim.display_name))

            watcher_lines: list[tuple[int, str]] = []
            for watcher_id, watched_id in watched.items():
                watched_player = player_map.get(watched_id)
                if watched_player is None:
                    continue
                visitor_names = [
                    self._tg_mention(visitor_id, player_map[visitor_id].display_name)
                    for visitor_id in visitors_by_target.get(watched_id, [])
                    if visitor_id in player_map
                ]
                if visitor_names:
                    text = (
                        f"🔎 Siz {self._tg_mention(watched_player.telegram_id, watched_player.display_name)}ni kuzatdingiz.\n"
                        "Uning oldiga kelganlar: " + ", ".join(visitor_names)
                    )
                else:
                    text = (
                        f"🔎 Siz {self._tg_mention(watched_player.telegram_id, watched_player.display_name)}ni kuzatdingiz.\n"
                        "Bu tunda uning oldiga hech kim kelmadi."
                    )
                watcher_lines.append((watcher_id, text))

            sorcerer_revenge_candidates: list[tuple[int, int]] = []
            for victim_id in list(dead):
                victim = player_map.get(victim_id)
                if victim and Role(victim.role) == Role.SORCERER:
                    attacker = None
                    if victim_id in active_mafia_kills:
                        mafia_actors = [
                            a.actor_telegram_id
                            for a in actions
                            if a.action_type == ActionType.KILL.value
                            and a.target_telegram_id == victim_id
                            and a.actor_telegram_id in player_map
                            and Role(player_map[a.actor_telegram_id].role) in active_mafia_roles
                        ]
                        attacker = mafia_actors[0] if mafia_actors else None
                    elif victim_id in killer_kills:
                        killer_actors = [
                            a.actor_telegram_id
                            for a in actions
                            if a.action_type == ActionType.KILL.value and a.details == "killer"
                        ]
                        attacker = killer_actors[0] if killer_actors else None
                    elif victim_id in commissar_shots:
                        shooter = [a.actor_telegram_id for a in actions if a.action_type == ActionType.SHOOT.value]
                        attacker = shooter[0] if shooter else None
                    if attacker:
                        sorcerer_revenge_candidates.append((victim_id, attacker))

            for _, attacker in sorcerer_revenge_candidates:
                if attacker in alive_ids:
                    dead.add(attacker)
                    death_causes[attacker] = "sorcerer"
                    death_visitors[attacker] = role_label(Role.SORCERER.value)

            for dead_id in dead:
                pl = player_map.get(dead_id)
                if pl:
                    pl.alive = False
                    pl.death_day = game.day_number + 1
                    if dead_id in mafia_dead:
                        if pl.last_words:
                            night_event_lines.append(self._last_words_line(pl, pl.last_words))
                        else:
                            pl.awaiting_last_words = True
                            last_words_prompts.append((pl.telegram_id, pl.display_name))

            night_event_lines.extend(self._apply_role_successions(alive_players, dead))

            game.phase = GamePhase.DAY_DISCUSSION.value
            game.day_number += 1
            self._add_game_log(
                session,
                game,
                "night_resolved",
                dead_ids=sorted(dead),
                transformed=transformed,
                blocked_ids=sorted(blocked),
                healed_ids=sorted(healed),
                guarded_ids=sorted(guarded),
                actions_count=len(actions),
            )
            await session.commit()

            chat_id = game.chat_id
            lang = await self.get_group_language(chat_id)
            alive_after_night = [player for player in alive_players if player.alive]
            dead_players = [player_map[player_id] for player_id in dead if player_id in player_map]
            day_caption = self._build_day_intro_text(game.day_number)
            alive_status = self._build_alive_status_text(alive_after_night)
            story_messages = self._build_night_story_messages(
                dead_players=dead_players,
                transformed=transformed,
                night_activity_lines=night_activity_lines,
                night_event_lines=night_event_lines,
                death_causes=death_causes,
                death_visitors=death_visitors,
            )
            doctor_help_ids = [
                player_id
                for player_id in doctor_help_targets
                if player_id in player_map and player_id not in dead
            ]

        await self._clear_night_prompt_buttons(bot, game_id, night)
        await self._send_phase_media(
            bot,
            chat_id,
            is_night=False,
            lang=lang,
            game_id=game_id,
            caption_override=day_caption,
        )
        await bot.send_message(chat_id, alive_status)
        for story_message in story_messages:
            await bot.send_message(chat_id, story_message)
            await asyncio.sleep(0.15)

        for telegram_id in doctor_help_ids:
            try:
                await bot.send_message(
                    telegram_id,
                    "Doktor sizga yordam berdi :)",
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        for telegram_id, text in protected_notices:
            try:
                await bot.send_message(
                    telegram_id,
                    text,
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        for telegram_id, text in miner_result_notices:
            try:
                await bot.send_message(
                    telegram_id,
                    text,
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        for line in dict.fromkeys(protected_group_lines):
            await bot.send_message(chat_id, line)

        for telegram_id, _ in last_words_prompts:
            try:
                await bot.send_message(
                    telegram_id,
                    "Mafiya sizni o'ldirdi. Guruhga boradigan so'nggi xabaringizni yozing.",
                )
            except TelegramForbiddenError:
                pass

        for commissar_id, target_id in checks:
            async with self.session_factory() as s2:
                target = (
                    await s2.execute(
                        select(GamePlayer).where(GamePlayer.game_id == game_id, GamePlayer.telegram_id == target_id)
                    )
                ).scalar_one_or_none()
                target_user = (
                    await s2.execute(select(User).where(User.telegram_id == target_id))
                ).scalar_one_or_none()
                hidden_by_item = False
                if target_user is not None and target_user.use_mask is not False and (target_user.mask or 0) > 0:
                    target_user.mask -= 1
                    hidden_by_item = True
                    await s2.commit()
                elif target_user is not None and target_user.use_fake_document is not False and (target_user.fake_document or 0) > 0:
                    target_user.fake_document -= 1
                    hidden_by_item = True
                    await s2.commit()
            if target is None:
                continue
            seen_role = Role(target.role)
            if Role(target.role) == Role.SPY:
                seen_role = Role.CITIZEN
            if target_id in defended and target.team == Team.MAFIA.value:
                seen_role = Role.CITIZEN
            if hidden_by_item:
                seen_role = Role.CITIZEN
                await bot.send_message(chat_id, "🎭 Kimdir tekshiruvdan yashirinish uchun maska yoki soxta hujjat ishlatdi.")
                try:
                    await bot.send_message(
                        target_id,
                        "🎭 Maska yoki 📁 soxta hujjat komissar tekshiruvini yashirdi.",
                        reply_markup=await self.group_return_keyboard(bot, chat_id),
                    )
                except TelegramForbiddenError:
                    pass
            try:
                await bot.send_message(commissar_id, self._commissar_check_result_text(target, seen_role))
            except TelegramForbiddenError:
                pass

        for observer_id, victim_name in witness_lines:
            try:
                await bot.send_message(observer_id, f"👀 Siz {victim_name} oldida qotillik izlarini ko'rdingiz.")
            except TelegramForbiddenError:
                pass

        for watcher_id, text in watcher_lines:
            try:
                await bot.send_message(watcher_id, text)
            except TelegramForbiddenError:
                pass

        winner = await self.check_winner(game_id)
        if winner:
            await self.finish_game(bot, game_id, winner)
            return

        scheduler.add_job(
            self.start_voting,
            "date",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=self.settings.day_discussion_timeout),
            args=[bot, game_id],
            id=f"discussion_end_{game_id}",
            replace_existing=True,
        )

    async def start_voting(self, bot: Bot, game_id: int) -> None:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value:
                return
            game.phase = GamePhase.DAY_VOTING.value
            alive = await self._alive_players(session, game_id)
            self._add_game_log(
                session,
                game,
                "voting_started",
                alive_count=len(alive),
                timeout=self.settings.day_voting_timeout,
            )
            await session.commit()
            choices = [(p.telegram_id, p.display_name) for p in alive]
            lang = await self.get_group_language(game.chat_id)

        if len(choices) <= 1:
            winner = await self.check_winner(game_id)
            if winner:
                await self.finish_game(bot, game_id, winner)
            return

        await bot.send_message(
            (await self._game_chat_id(game_id)),
            "Aybdorlarni aniqlash va jazolash vaqti keldi.\n"
            f"Ovoz berish uchun {self.settings.day_voting_timeout} sekund.",
            reply_markup=go_vote_private_keyboard(self.settings, game_id),
        )
        for player_id, _ in choices:
            ok, _ = await self.send_private_vote_menu(bot, game_id, player_id)
            if not ok:
                continue
        scheduler.add_job(
            self.resolve_voting,
            "date",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=self.settings.day_voting_timeout),
            args=[bot, game_id],
            id=f"vote_end_{game_id}",
            replace_existing=True,
        )

    async def cast_vote(
        self,
        bot: Bot,
        game_id: int,
        voter_id: int,
        target_id: int,
    ) -> tuple[bool, str]:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value or game.phase != GamePhase.DAY_VOTING.value:
                return False, t(self.settings.default_language, "callback_expired")

            voter = (
                await session.execute(select(GamePlayer).where(GamePlayer.game_id == game_id, GamePlayer.telegram_id == voter_id))
            ).scalar_one_or_none()
            target = (
                await session.execute(select(GamePlayer).where(GamePlayer.game_id == game_id, GamePlayer.telegram_id == target_id))
            ).scalar_one_or_none()
            if voter is None or not voter.alive:
                return False, t(self.settings.default_language, "not_alive")
            if target is None or not target.alive:
                return False, "Nishon o'lik yoki topilmadi."
            target_display_name = target.display_name or "Unknown"
            skipped = (
                await session.execute(
                    select(SkipDecision.id).where(
                        SkipDecision.game_id == game_id,
                        SkipDecision.phase == "vote",
                        SkipDecision.day_number == game.day_number,
                        SkipDecision.night_number == game.night_number,
                        SkipDecision.user_telegram_id == voter_id,
                    )
                )
            ).scalar_one_or_none()
            if skipped is not None:
                return False, "Siz ovoz berishni o'tkazib yuborgansiz."

            exists = (
                await session.execute(
                    select(Vote).where(
                        Vote.game_id == game_id,
                        Vote.day_number == game.day_number,
                        Vote.voter_telegram_id == voter_id,
                    )
                )
            ).scalar_one_or_none()
            if exists is not None:
                return False, t(self.settings.default_language, "vote_already")

            session.add(
                Vote(
                    game_id=game_id,
                    day_number=game.day_number,
                    voter_telegram_id=voter_id,
                    target_telegram_id=target_id,
                )
            )
            self._add_game_log(session, game, "vote_cast", actor=voter, target=target)
            await session.commit()
            chat_id = game.chat_id
            voter_name = self._tg_mention(voter.telegram_id, voter.display_name)
            target_name = self._tg_mention(target.telegram_id, target_display_name)

        await bot.send_message(chat_id, f"{voter_name} ovoz berdi {target_name} ga")
        try:
            await bot.send_message(
                voter_id,
                f"Siz - {escape(target_display_name)} ni tanladingiz.",
                reply_markup=await self.group_return_keyboard(bot, chat_id),
            )
        except TelegramForbiddenError:
            pass
        return True, f"Siz {target_display_name} ni tanladingiz."

    async def send_private_vote_menu(self, bot: Bot, game_id: int, voter_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value or game.phase != GamePhase.DAY_VOTING.value:
                return False, t(self.settings.default_language, "callback_expired")
            voter = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == voter_id,
                        GamePlayer.alive.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if voter is None:
                return False, t(self.settings.default_language, "not_alive")
            already_voted = (
                await session.execute(
                    select(Vote.id).where(
                        Vote.game_id == game_id,
                        Vote.day_number == game.day_number,
                        Vote.voter_telegram_id == voter_id,
                    )
                )
            ).scalar_one_or_none()
            if already_voted is not None:
                return False, t(self.settings.default_language, "vote_already")
            already_skipped = (
                await session.execute(
                    select(SkipDecision.id).where(
                        SkipDecision.game_id == game_id,
                        SkipDecision.phase == "vote",
                        SkipDecision.day_number == game.day_number,
                        SkipDecision.night_number == game.night_number,
                        SkipDecision.user_telegram_id == voter_id,
                    )
                )
            ).scalar_one_or_none()
            if already_skipped is not None:
                return False, "Siz ovoz berishni o'tkazib yuborgansiz."
            alive = await self._alive_players(session, game_id)

        choices = [(p.telegram_id, p.display_name) for p in alive if p.telegram_id != voter_id]
        await bot.send_message(
            voter_id,
            "🗳 <b>Ovoz berish</b>\n\nKimni kunduzgi yig'ilishda osamiz?",
            reply_markup=vote_keyboard(game_id, choices),
        )
        return True, "Ovoz berish ro'yxati yuborildi."

    async def set_last_words(self, telegram_id: int, words: str) -> tuple[bool, str]:
        cleaned = words.strip()
        if not cleaned:
            return False, "Xabar bo'sh bo'lmasin."
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(GamePlayer, Game)
                    .join(Game, Game.id == GamePlayer.game_id)
                    .where(
                        Game.status == GameStatus.ACTIVE.value,
                        GamePlayer.telegram_id == telegram_id,
                    )
                    .order_by(Game.id.desc())
                )
            ).first()
            if row is None:
                return False, "Siz aktiv o'yinda emassiz."
            player, game = row
            player.last_words = cleaned[:500]
            player.awaiting_last_words = False
            self._add_game_log(
                session,
                game,
                "last_words_saved",
                actor=player,
                text_length=len(player.last_words),
            )
            await session.commit()
            return True, "So'nggi xabaringiz saqlandi."

    async def handle_pending_last_words(self, bot: Bot, telegram_id: int, words: str) -> bool:
        cleaned = words.strip()
        if not cleaned:
            return False
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(GamePlayer, Game)
                    .join(Game, Game.id == GamePlayer.game_id)
                    .where(
                        Game.status == GameStatus.ACTIVE.value,
                        GamePlayer.telegram_id == telegram_id,
                        GamePlayer.awaiting_last_words.is_(True),
                    )
                    .order_by(Game.id.desc())
                )
            ).first()
            if row is None:
                return False
            player, game = row
            player.last_words = cleaned[:500]
            player.awaiting_last_words = False
            line = self._last_words_line(player, player.last_words)
            chat_id = game.chat_id
            self._add_game_log(
                session,
                game,
                "last_words_sent",
                actor=player,
                text_length=len(player.last_words),
            )
            await session.commit()

        await bot.send_message(chat_id, line)
        return True

    async def resolve_voting(self, bot: Bot, game_id: int) -> None:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value:
                return
            if game.phase != GamePhase.DAY_VOTING.value:
                return

            votes = (
                await session.execute(
                    select(Vote).where(Vote.game_id == game_id, Vote.day_number == game.day_number)
                )
            ).scalars().all()
            alive = await self._alive_players(session, game_id)
            alive_map = {p.telegram_id: p for p in alive}

            counter = Counter(v.target_telegram_id for v in votes)
            chat_id = game.chat_id
            if not counter:
                self._add_game_log(session, game, "voting_resolved", result="no_votes", votes_count=0)
                await bot.send_message(
                    chat_id,
                    "<b>Ovoz berish natijalari:</b>\n"
                    "0 👍  |  0 👎\n\n"
                    "Aholi janjallashib uylariga tarqashdi.",
                )
                await self._apply_inactivity_after_vote(bot, session, game, votes)
                winner = await self.check_winner(game_id)
                if winner:
                    await self.finish_game(bot, game_id, winner)
                    return
                game.phase = GamePhase.NIGHT.value
                game.night_number += 1
                await session.commit()
                await self.start_night(bot, game_id)
                return

            top_count = counter.most_common(1)[0][1]
            top_targets = [target_id for target_id, count in counter.items() if count == top_count]
            if len(top_targets) > 1:
                self._add_game_log(
                    session,
                    game,
                    "voting_resolved",
                    result="tie",
                    top_targets=top_targets,
                    top_count=top_count,
                )
                await bot.send_message(chat_id, "Aholi janjallashib uylariga tarqashdi.")
                await self._apply_inactivity_after_vote(bot, session, game, votes)
                winner = await self.check_winner(game_id)
                if winner:
                    await self.finish_game(bot, game_id, winner)
                    return
                game.phase = GamePhase.NIGHT.value
                game.night_number += 1
                await session.commit()
                await self.start_night(bot, game_id)
                return

            target_id = top_targets[0]
            target = alive_map.get(target_id)
            if target is None:
                return
            judges = [
                player
                for player in alive
                if Role(player.role) == Role.JUDGE and not player.judge_cancel_used
            ]
            game.phase = GamePhase.DAY_CONFIRM.value
            self._add_game_log(
                session,
                game,
                "hang_confirmation_started",
                target=target,
                votes_count=len(votes),
                top_count=top_count,
                judges_count=len(judges),
            )
            await session.commit()
            confirm_message = await bot.send_message(
                chat_id,
                f"Rostdan xam {self._tg_mention(target.telegram_id, target.display_name)}ni osmoqchimisiz?",
                reply_markup=confirm_hang_keyboard(game_id, target.telegram_id),
            )
            scheduler.add_job(
                self.resolve_hang_confirmation,
                "date",
                run_date=datetime.now(timezone.utc) + timedelta(seconds=30),
                args=[bot, game_id, target.telegram_id, confirm_message.message_id],
                id=f"hang_confirm_{game_id}",
                replace_existing=True,
                misfire_grace_time=30,
            )
            for judge in judges:
                try:
                    await bot.send_message(
                        judge.telegram_id,
                        "🧑‍⚖️ <b>Sudya qarori</b>\n\n"
                        f"Aholi {self._tg_mention(target.telegram_id, target.display_name)}ni osmoqchi. "
                        "O'yinda bir marta bu hukmni bekor qilishingiz mumkin.",
                        reply_markup=judge_cancel_keyboard(
                            game_id,
                            target.telegram_id,
                            judge.telegram_id,
                            confirm_message.message_id,
                        ),
                    )
                except TelegramForbiddenError:
                    pass
            return

    async def _apply_inactivity_after_vote(
        self,
        bot: Bot,
        session: AsyncSession,
        game: Game,
        votes: list[Vote],
    ) -> None:
        game_id = game.id
        chat_id = game.chat_id

        active_ids = {action.actor_telegram_id for action in (
                await session.execute(
                    select(NightAction).where(
                        NightAction.game_id == game_id,
                        NightAction.night_number == game.night_number,
                    )
                )
            ).scalars().all()}
        active_ids.update(vote.voter_telegram_id for vote in votes)
        active_ids.update(
            hang_vote.voter_telegram_id
            for hang_vote in (
                await session.execute(
                    select(HangVote).where(
                        HangVote.game_id == game_id,
                        HangVote.day_number == game.day_number,
                    )
                )
            ).scalars().all()
        )
        active_ids.update(
            skip.user_telegram_id
            for skip in (
                await session.execute(
                    select(SkipDecision).where(
                        SkipDecision.game_id == game_id,
                        SkipDecision.day_number == game.day_number,
                    )
                )
            ).scalars().all()
        )

        alive_after_vote = await self._alive_players(session, game_id)
        sleep_lines: list[str] = []
        for player in alive_after_vote:
            if player.telegram_id in active_ids:
                player.inactive_rounds = 0
                continue
            player.inactive_rounds = (player.inactive_rounds or 0) + 1
            if player.inactive_rounds >= 2:
                player.alive = False
                player.death_day = game.day_number
                self._add_game_log(
                    session,
                    game,
                    "player_removed_for_inactivity",
                    actor=player,
                    inactive_rounds=player.inactive_rounds,
                )
                sleep_lines.append(self._sleep_death_line(player))
        await session.commit()

        for line in sleep_lines:
            await bot.send_message(chat_id, line)

    async def confirm_hang(
        self,
        bot: Bot,
        game_id: int,
        target_id: int,
        confirmed: bool,
        voter_id: int,
    ) -> tuple[bool, str, Optional[object]]:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value or game.phase != GamePhase.DAY_CONFIRM.value:
                return False, t(self.settings.default_language, "callback_expired"), None
            voter = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == voter_id,
                        GamePlayer.alive.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if voter is None:
                return False, "Siz bu o'yinda tirik ishtirokchi emassiz.", None
            target = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == target_id,
                        GamePlayer.alive.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if target is None:
                return False, "Nomzod topilmadi yoki allaqachon o'lgan.", None
            if voter_id == target_id:
                return False, "O'zingiz uchun ovoz bera olmaysiz.", None
            skipped = (
                await session.execute(
                    select(SkipDecision.id).where(
                        SkipDecision.game_id == game_id,
                        SkipDecision.phase == "hang",
                        SkipDecision.day_number == game.day_number,
                        SkipDecision.night_number == game.night_number,
                        SkipDecision.user_telegram_id == voter_id,
                    )
                )
            ).scalar_one_or_none()
            if skipped is not None:
                return False, "Siz osish tanlovini o'tkazib yuborgansiz.", None
            existing = (
                await session.execute(
                    select(HangVote).where(
                        HangVote.game_id == game_id,
                        HangVote.day_number == game.day_number,
                        HangVote.voter_telegram_id == voter_id,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                return False, "Siz ovoz berdingiz", None
            else:
                session.add(
                    HangVote(
                        game_id=game_id,
                        day_number=game.day_number,
                        target_telegram_id=target_id,
                        voter_telegram_id=voter_id,
                        approve=confirmed,
                    )
                )
            hang_votes = (
                await session.execute(
                    select(HangVote).where(
                        HangVote.game_id == game_id,
                        HangVote.day_number == game.day_number,
                        HangVote.target_telegram_id == target_id,
                    )
                )
            ).scalars().all()
            yes_count = sum(1 for vote in hang_votes if vote.approve)
            no_count = sum(1 for vote in hang_votes if not vote.approve)
            self._add_game_log(
                session,
                game,
                "hang_vote_cast",
                actor=voter,
                target=target,
                confirmed=confirmed,
                yes_count=yes_count,
                no_count=no_count,
            )
            chat_id = game.chat_id
            target_display_name = target.display_name
            await session.commit()
            keyboard = confirm_hang_keyboard(game_id, target_id, yes_count=yes_count, no_count=no_count)
            try:
                await bot.send_message(
                    voter_id,
                    "Siz ovoz berdingiz.",
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass
            return True, "Ovozingiz qabul qilindi.", keyboard

    async def judge_cancel_hang(
        self,
        bot: Bot,
        game_id: int,
        target_id: int,
        judge_id: int,
        confirm_message_id: Optional[int] = None,
    ) -> tuple[bool, str]:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value or game.phase != GamePhase.DAY_CONFIRM.value:
                return False, t(self.settings.default_language, "callback_expired")
            judge = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == judge_id,
                        GamePlayer.alive.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if judge is None or Role(judge.role) != Role.JUDGE:
                return False, "Bu qaror faqat Sudya uchun."
            if judge.judge_cancel_used:
                return False, "Sudya hukmni faqat bir marta bekor qila oladi."
            target = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == target_id,
                    )
                )
            ).scalar_one_or_none()
            if target is None:
                return False, "Nomzod topilmadi."

            votes = (
                await session.execute(
                    select(Vote).where(Vote.game_id == game_id, Vote.day_number == game.day_number)
                )
            ).scalars().all()
            chat_id = game.chat_id
            target_name = self._tg_mention(target.telegram_id, target.display_name)
            judge_name = self._tg_mention(judge.telegram_id, judge.display_name)

            await self._apply_inactivity_after_vote(bot, session, game, votes)
            winner = await self.check_winner(game_id)
            if winner:
                await self.finish_game(bot, game_id, winner)
                return True, "O'yin yakunlandi."

            judge.judge_cancel_used = True
            judge.inactive_rounds = 0
            game.phase = GamePhase.NIGHT.value
            game.night_number += 1
            self._add_game_log(
                session,
                game,
                "hang_cancelled_by_judge",
                actor=judge,
                target=target,
            )
            await session.commit()

        job = scheduler.get_job(f"hang_confirm_{game_id}")
        if job:
            job.remove()
        if confirm_message_id:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=confirm_message_id,
                    reply_markup=None,
                )
            except TelegramBadRequest:
                pass
        await bot.send_message(
            chat_id,
            f"🧑‍⚖️ Sudya {judge_name} kunduzgi hukmni bekor qildi.\n"
            f"{target_name} osilmadi. Aholi tarqaldi...",
        )
        try:
            await bot.send_message(
                judge_id,
                f"Siz {target.display_name} uchun kunduzgi hukmni bekor qildingiz.",
                reply_markup=await self.group_return_keyboard(bot, chat_id),
            )
        except TelegramForbiddenError:
            pass
        await self.start_night(bot, game_id)
        return True, "Sudya qarori qabul qilindi."

    async def resolve_hang_confirmation(
        self,
        bot: Bot,
        game_id: int,
        target_id: int,
        message_id: Optional[int] = None,
    ) -> None:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value or game.phase != GamePhase.DAY_CONFIRM.value:
                return
            target = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == target_id,
                        GamePlayer.alive.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if target is None:
                return
            votes = (
                await session.execute(
                    select(Vote).where(Vote.game_id == game_id, Vote.day_number == game.day_number)
                )
            ).scalars().all()
            hang_votes = (
                await session.execute(
                    select(HangVote).where(
                        HangVote.game_id == game_id,
                        HangVote.day_number == game.day_number,
                        HangVote.target_telegram_id == target_id,
                    )
                )
            ).scalars().all()
            chat_id = game.chat_id
            yes_confirm = sum(1 for vote in hang_votes if vote.approve)
            no_confirm = sum(1 for vote in hang_votes if not vote.approve)

            if message_id:
                try:
                    await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
                except TelegramBadRequest:
                    pass

            if yes_confirm > no_confirm:
                yes_votes = sum(1 for vote in votes if vote.target_telegram_id == target_id)
                target_user = (
                    await session.execute(select(User).where(User.telegram_id == target.telegram_id))
                ).scalar_one_or_none()
                if target_user is not None and target_user.use_vote_protection is not False and (target_user.vote_protection or 0) > 0:
                    target_user.vote_protection -= 1
                    self._add_game_log(
                        session,
                        game,
                        "hang_blocked_by_vote_protection",
                        target=target,
                        remaining_vote_protection=target_user.vote_protection,
                    )
                    await session.commit()
                    await bot.send_message(
                        chat_id,
                        f"⚖️ {self._tg_mention(target.telegram_id, target.display_name)} o'z himoyasini ishlatdi. Osishni bekor qildi.",
                    )
                    try:
                        await bot.send_message(
                            target.telegram_id,
                            "⚖️ Ovoz himoyasi sizni osilishdan saqlab qoldi.",
                            reply_markup=await self.group_return_keyboard(bot, chat_id),
                        )
                    except TelegramForbiddenError:
                        pass
                else:
                    target.alive = False
                    target.death_day = game.day_number
                    self._add_game_log(
                        session,
                        game,
                        "player_hanged",
                        target=target,
                        yes_confirm=yes_confirm,
                        no_confirm=no_confirm,
                        vote_count=yes_votes,
                    )
                    vote_text = (
                        "<b>Ovoz berish natijalari:</b>\n"
                        f"{yes_votes} 👍  |  {no_confirm} 👎\n\n"
                        f"{self._tg_mention(target.telegram_id, target.display_name)} O'tkazilgan kunduzgi yiģilishda osildi!\n"
                        f"U edi {role_label(target.role)}.."
                    )
                    await session.commit()
                    await bot.send_message(chat_id, vote_text)

                    if Role(target.role) == Role.JESTER:
                        await bot.send_message(
                            chat_id,
                            f"🎭 Masxaraboz {self._tg_mention(target.telegram_id, target.display_name)} "
                            "o'z xohishiga yetdi va alohida g'olib bo'ldi!"
                        )
                        await self.finish_game(bot, game_id, Team.NEUTRAL)
                        return

                    if Role(target.role) == Role.SORCERER:
                        alive_now = await self._alive_players(session, game_id)
                        if alive_now:
                            victim = alive_now[0]
                            victim.alive = False
                            victim.death_day = game.day_number
                            self._add_game_log(
                                session,
                                game,
                                "sorcerer_revenge_after_hang",
                                actor=target,
                                target=victim,
                            )
                            await session.commit()
                            await bot.send_message(chat_id, f"🧞‍♂️ Afsungar bilan birga {victim.display_name} ham ketdi.")
            else:
                self._add_game_log(
                    session,
                    game,
                    "hang_rejected",
                    target=target,
                    yes_confirm=yes_confirm,
                    no_confirm=no_confirm,
                )
                await bot.send_message(chat_id, "Aholi janjallashib uylariga tarqashdi.")

            await self._apply_inactivity_after_vote(bot, session, game, votes)
            winner = await self.check_winner(game_id)
            if winner:
                await self.finish_game(bot, game_id, winner)
                return

            game.phase = GamePhase.NIGHT.value
            game.night_number += 1
            await session.commit()

        await self.start_night(bot, game_id)

    async def check_winner(self, game_id: int) -> Optional[Team]:
        async with self.session_factory() as session:
            alive = (
                await session.execute(select(GamePlayer).where(GamePlayer.game_id == game_id, GamePlayer.alive.is_(True)))
            ).scalars().all()
            if not alive:
                return Team.CITY

            teams = [p.team for p in alive]

            if len(alive) == 1 and alive[0].team == Team.KILLER.value:
                return Team.KILLER
            if len(alive) == 1 and alive[0].team == Team.NEUTRAL.value:
                return Team.NEUTRAL

            mafia_count = sum(1 for t in teams if t == Team.MAFIA.value)
            city_count = sum(1 for t in teams if t == Team.CITY.value)
            killer_count = sum(1 for t in teams if t == Team.KILLER.value)
            non_mafia_count = len(alive) - mafia_count

            # Shaharga xavf soladigan asosiy kuchlar qolmasa, o'yin darhol yopiladi.
            if mafia_count == 0 and killer_count == 0:
                return Team.CITY

            # Final duel: Don/Mafiya/Josus va bitta boshqa personaj qolsa, mafia ustun.
            if len(alive) == 2 and mafia_count == 1 and non_mafia_count == 1:
                return Team.MAFIA

            # Mafia soni qolgan tinch aholidan kam bo'lmasa, shahar nazorati mafiyaga o'tadi.
            if mafia_count > 0 and mafia_count >= city_count and killer_count == 0:
                return Team.MAFIA
            if len(alive) == 1 and killer_count == 1:
                return Team.KILLER
            return None

    async def finish_game(self, bot: Bot, game_id: int, winner_team: Team) -> None:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None:
                return
            if game.status in {GameStatus.COMPLETED.value, GameStatus.CANCELLED.value}:
                return

            players = (
                await session.execute(select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.id.asc()))
            ).scalars().all()
            users = {
                u.telegram_id: u
                for u in (
                    await session.execute(select(User).where(User.telegram_id.in_([p.telegram_id for p in players])))
                ).scalars().all()
            }

            game.status = GameStatus.COMPLETED.value
            game.phase = GamePhase.ENDED.value
            game.active_key = None
            game.winner_team = winner_team.value
            game.ended_at = datetime.now(timezone.utc)

            winners: list[GamePlayer] = []
            losers: list[GamePlayer] = []
            for p in players:
                is_winner = p.team == winner_team.value
                if winner_team == Team.KILLER:
                    is_winner = p.team == Team.KILLER.value and p.alive
                elif winner_team == Team.NEUTRAL:
                    is_winner = p.team == Team.NEUTRAL.value and p.alive
                if p.role == Role.MINER.value and p.alive:
                    is_winner = True
                p.won = is_winner
                user = users.get(p.telegram_id)
                if user:
                    user.total_games += 1
                    if is_winner:
                        user.wins += 1
                        user.dollar += self.settings.winner_reward_dollar
                        user.diamonds += self.settings.winner_reward_diamond
                    else:
                        user.dollar += self.settings.loser_reward_dollar
                        user.diamonds += self.settings.loser_reward_diamond
                (winners if is_winner else losers).append(p)

            self._add_game_log(
                session,
                game,
                "game_finished",
                winner_team=winner_team.value,
                winners=[p.telegram_id for p in winners],
                losers=[p.telegram_id for p in losers],
                players_count=len(players),
            )
            await session.commit()
            chat_id = game.chat_id
            if game.ended_at and game.started_at:
                ended_at = self._ensure_utc(game.ended_at)
                started_at = self._ensure_utc(game.started_at)
                duration_seconds = max(0, int((ended_at - started_at).total_seconds()))
            else:
                duration_seconds = 0

        self._cleanup_jobs(game_id)
        self._invalidate_game_cache(chat_id)

        winner_lines = [
            f"{idx}. {self._tg_mention(p.telegram_id, p.display_name)} - {role_label(p.role)}"
            for idx, p in enumerate(winners, 1)
        ]
        other_lines = [
            f"{idx}. {self._tg_mention(p.telegram_id, p.display_name)} - {role_label(p.role)}"
            for idx, p in enumerate([player for player in players if player.alive and player not in winners], 1)
        ]
        winners_block = "\n".join(winner_lines) if winner_lines else "-"
        others_block = "\n".join(other_lines) if other_lines else "-"

        text = (
            "<b>O'yin tugadi!</b>\n\n"
            "G'oliblar:\n"
            f"{winners_block}\n\n"
            "Qolgan o'yinchilar:\n"
            f"{others_block}\n\n"
            f"O'yin: {self._format_duration(duration_seconds)} davom etdi"
        )
        await bot.send_message(chat_id, text)

        async with self.session_factory() as session:
            users = {
                u.telegram_id: u
                for u in (
                    await session.execute(select(User).where(User.telegram_id.in_([p.telegram_id for p in players])))
                ).scalars().all()
            }

        news_url = await self.get_news_channel_url()
        for p in players:
            user = users.get(p.telegram_id)
            if user is None:
                continue
            result_title = "you_win" if p.won else "you_lose"
            reward_dollar = self.settings.winner_reward_dollar if p.won else self.settings.loser_reward_dollar
            reward_diamond = self.settings.winner_reward_diamond if p.won else self.settings.loser_reward_diamond
            body = (
                f"{t(user.language, result_title, dollar=reward_dollar, diamond=reward_diamond)}\n\n"
                f"{self.format_user_dashboard(user)}"
            )
            try:
                await bot.send_message(
                    p.telegram_id,
                    body,
                    reply_markup=profile_dashboard_keyboard(
                        self.settings,
                        user=user,
                        is_admin=p.telegram_id in self.settings.admin_ids,
                        news_url=news_url,
                    ),
                )
            except TelegramForbiddenError:
                pass

    @staticmethod
    def _format_duration(seconds: int) -> str:
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours} soat {minutes} daqiqa {sec} soniya"
        if minutes:
            return f"{minutes} daqiqa {sec} soniya"
        return f"{sec} soniya"

    @staticmethod
    def format_user_dashboard(user: User) -> str:
        def state(value: bool) -> str:
            return "🟢 ON" if value is not False else "🔴 OFF"

        display_name = GameEngine._tg_mention(user.telegram_id, user.display_name)
        return (
            f"👤 Nik: {display_name}\n"
            f"⭐ ID: <code>{user.telegram_id}</code>\n\n"
            f"💵 Dollar: <b>{user.dollar}</b>\n"
            f"💎 Olmos: <b>{user.diamonds}</b>\n\n"
            f"🛡 Himoya: <b>{user.protection}</b> {state(user.use_protection)}\n"
            f"⛑ Qotildan himoya: <b>{user.killer_protection}</b> {state(user.use_killer_protection)}\n"
            f"⚖️ Ovoz berishni himoya qilish: <b>{user.vote_protection}</b> {state(user.use_vote_protection)}\n"
            f"👷 Konchi himoyasi: <b>{user.miner_protection}</b> {state(user.use_miner_protection)}\n"
            f"🔫 Miltiq: <b>{user.gun}</b> {state(user.use_gun)}\n\n"
            f"🎭 Maska: <b>{user.mask}</b> {state(user.use_mask)}\n"
            f"📁 Soxta hujjat: <b>{user.fake_document}</b> {state(user.use_fake_document)}\n"
            f"🃏 Keyingi o'yindagi rolingiz: <b>{user.next_game_role or '-'}</b>\n\n"
            f"🎯 Побед: <b>{user.wins}</b>\n"
            f"🎲 Всего игр: <b>{user.total_games}</b>"
        )

    async def user_in_running_game(self, telegram_id: int) -> bool:
        async with self.session_factory() as session:
            player_id = (
                await session.execute(
                    select(GamePlayer.id)
                    .join(Game, Game.id == GamePlayer.game_id)
                    .where(
                        Game.status == GameStatus.ACTIVE.value,
                        GamePlayer.telegram_id == telegram_id,
                        GamePlayer.alive.is_(True),
                    )
                    .order_by(Game.id.desc())
                )
            ).scalar_one_or_none()
            return player_id is not None

    async def use_gun(self, bot: Bot, chat_id: int, shooter_id: int, target_id: int) -> tuple[bool, str]:
        if shooter_id == target_id:
            return False, "O'zingizga miltiq ishlata olmaysiz."

        async with self.session_factory() as session:
            game = await self.find_active_game(session, chat_id)
            if game is None or game.status != GameStatus.ACTIVE.value:
                return False, "Aktiv o'yin topilmadi."

            shooter = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game.id,
                        GamePlayer.telegram_id == shooter_id,
                        GamePlayer.alive.is_(True),
                    )
                )
            ).scalar_one_or_none()
            target = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game.id,
                        GamePlayer.telegram_id == target_id,
                        GamePlayer.alive.is_(True),
                    )
                )
            ).scalar_one_or_none()
            user = (await session.execute(select(User).where(User.telegram_id == shooter_id))).scalar_one_or_none()
            target_user = (await session.execute(select(User).where(User.telegram_id == target_id))).scalar_one_or_none()

            if shooter is None:
                return False, "Siz bu o'yinda tirik ishtirokchi emassiz."
            if target is None:
                return False, "Nishon topilmadi yoki allaqachon o'lgan."
            if user is None or (user.gun or 0) <= 0:
                return False, "Profilingizda miltiq yo'q. Do'kondan sotib oling."
            if user.use_gun is False:
                return False, "Profilingizda miltiq OFF holatda. Ishlatish uchun profil panelidan ON qiling."

            user.gun -= 1
            shooter.inactive_rounds = 0
            blocked_by_protection = (
                target_user is not None
                and target_user.use_protection is not False
                and (target_user.protection or 0) > 0
            )
            if blocked_by_protection:
                target_user.protection -= 1
                self._add_game_log(
                    session,
                    game,
                    "gun_blocked_by_protection",
                    actor=shooter,
                    target=target,
                    remaining_gun=user.gun,
                    remaining_protection=target_user.protection,
                )
            else:
                target.alive = False
                target.death_day = game.day_number
                self._add_game_log(
                    session,
                    game,
                    "gun_used",
                    actor=shooter,
                    target=target,
                    remaining_gun=user.gun,
                )
            target_name = self._tg_mention(target.telegram_id, target.display_name)
            shooter_name = self._tg_mention(shooter.telegram_id, shooter.display_name)
            game_id = game.id
            await session.commit()

        if blocked_by_protection:
            await bot.send_message(
                chat_id,
                f"🔫 {shooter_name} miltiq ishlatdi.\n"
                f"🛡 {target_name} o'z himoyasini ishlatdi. Miltiqdan omon qoldi.",
            )
            try:
                await bot.send_message(
                    target_id,
                    "🛡 Himoya sizni miltiqdan saqlab qoldi.",
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass
            return True, "Miltiq ishlatildi, lekin nishon himoyalangan ekan."

        await bot.send_message(
            chat_id,
            f"🔫 Kimdir miltiqdan foydalandi.\n"
            f"💀 {target_name} itdek otib tashlandi.\n"
            f"U edi {role_label(target.role)}.",
        )
        winner = await self.check_winner(game_id)
        if winner:
            await self.finish_game(bot, game_id, winner)
        return True, "Miltiq ishlatildi."

    async def active_game_for_chat(self, chat_id: int) -> Optional[Game]:
        async with self.session_factory() as session:
            return await self.find_active_game(session, chat_id)

    async def should_delete_message_for_non_player(self, chat_id: int, user_id: int) -> bool:
        now = self._monotonic()
        cached = self._active_participants_cache.get(chat_id)
        if cached and cached[0] > now:
            _, game_id, participant_ids = cached
            if game_id is None:
                return False
            return user_id not in participant_ids

        async with self.session_factory() as session:
            game = await self.find_active_game(session, chat_id)
            if game is None or game.status != GameStatus.ACTIVE.value:
                self._prune_cache_if_needed(self._active_participants_cache)
                self._active_participants_cache[chat_id] = (
                    now + self._cache_ttl_seconds,
                    None,
                    frozenset(),
                )
                return False
            participant_ids = frozenset(
                (
                    await session.execute(
                        select(GamePlayer.telegram_id).where(GamePlayer.game_id == game.id)
                    )
                ).scalars().all()
            )

        self._prune_cache_if_needed(self._active_participants_cache)
        self._active_participants_cache[chat_id] = (
            now + self._cache_ttl_seconds,
            game.id,
            participant_ids,
        )
        return user_id not in participant_ids

    async def get_player_running_game(self, telegram_id: int) -> Optional[tuple[int, GamePlayer, str]]:
        """Get the player's currently active game info and their player record.
        Returns: (game_id, game_chat_id, player)
        """
        async with self.session_factory() as session:
            result = (
                await session.execute(
                    select(Game, GamePlayer)
                    .join(GamePlayer, GamePlayer.game_id == Game.id)
                    .where(
                        Game.status == GameStatus.ACTIVE.value,
                        GamePlayer.telegram_id == telegram_id,
                    )
                    .order_by(Game.id.desc())
                )
            ).first()
            if result is None:
                return None
            game, player = result
            # Detach and return only necessary data
            return (game.id, game.chat_id, player.telegram_id, player.role, player.display_name, player.alive)

    async def can_send_private_team_message(self, telegram_id: int) -> bool:
        game_result = await self.get_player_running_game(telegram_id)
        if game_result is None:
            return False
        _, _, _, player_role, _, is_alive = game_result
        if not is_alive:
            return False
        try:
            role = Role(player_role)
        except ValueError:
            return False
        return role in {
            Role.DON,
            Role.MAFIA,
            Role.SPY,
            Role.HIRED_KILLER,
            Role.LAWYER,
            Role.DOCTOR,
            Role.COMMISSAR,
            Role.SERGEANT,
        }

    async def send_team_message_to_group(
        self,
        bot: Bot,
        telegram_id: int,
        message_text: str,
    ) -> tuple[bool, str]:
        """Forward a private bot message only to the sender's alive teammates."""
        game_result = await self.get_player_running_game(telegram_id)
        if game_result is None:
            return False, "Siz hozir aktiv o'yinda emas."
        
        game_id, chat_id, player_telegram_id, player_role, player_display_name, is_alive = game_result
        
        if not is_alive:
            return False, "O'lgan o'yinchilar dastaga xabar yuborishi mumkin emas."
        
        # Determine team and get team members
        team_members = []
        team_title = ""
        
        role = Role(player_role)
        
        # Mafia team
        if role in {Role.DON, Role.MAFIA, Role.SPY, Role.HIRED_KILLER, Role.LAWYER}:
            async with self.session_factory() as session:
                team_members = (
                    await session.execute(
                        select(GamePlayer).where(
                            GamePlayer.game_id == game_id,
                            GamePlayer.role.in_([
                                Role.DON.value, Role.MAFIA.value, Role.SPY.value,
                                Role.HIRED_KILLER.value, Role.LAWYER.value
                            ]),
                            GamePlayer.alive.is_(True),
                        )
                    )
                ).scalars().all()
            team_title = "🤵🏻 Mafia"
        
        # Doctors group
        elif role == Role.DOCTOR:
            async with self.session_factory() as session:
                doctors = (
                    await session.execute(
                        select(GamePlayer).where(
                            GamePlayer.game_id == game_id,
                            GamePlayer.role == Role.DOCTOR.value,
                            GamePlayer.alive.is_(True),
                        )
                    )
                ).scalars().all()
            if len(doctors) > 1:
                team_members = doctors
                team_title = "👨🏼‍⚕️ Doktorlar"
            else:
                return False, "Sizning dastada boshqa a'zolar yo'q."
        
        # Commissar and Sergeants group
        elif role in {Role.COMMISSAR, Role.SERGEANT}:
            async with self.session_factory() as session:
                team_members = (
                    await session.execute(
                        select(GamePlayer).where(
                            GamePlayer.game_id == game_id,
                            GamePlayer.role.in_([Role.COMMISSAR.value, Role.SERGEANT.value]),
                            GamePlayer.alive.is_(True),
                        )
                    )
                ).scalars().all()
            team_title = "🕵🏼 Komissar va Serjantlar"
        
        else:
            return False, "Sizning roli dastaga xabar yuborish huquqiga ega emas."
        
        recipients = [member for member in team_members if member.telegram_id != telegram_id]
        if not recipients:
            return False, "Sizning dastada boshqa tirik a'zo yo'q."
        
        safe_message = escape(message_text.strip()[:500])
        sender_name = self._tg_mention(player_telegram_id, player_display_name)
        private_message = (
            f"<b>{team_title}</b> - {role_label(role)}\n"
            f"{sender_name}: {safe_message}"
        )

        sent = 0
        failed = 0
        for member in recipients:
            try:
                await bot.send_message(
                    member.telegram_id,
                    private_message,
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
                sent += 1
            except TelegramForbiddenError:
                failed += 1
            except Exception as e:
                failed += 1
                logger.exception("Failed to send private team message: %s", e)

        if sent == 0:
            return False, "Sheriklaringiz bot private chatini ochmagan."
        if failed:
            return True, f"Xabar {sent} ta sherikka yuborildi. {failed} tasiga yuborilmadi."
        return True, f"Xabar {sent} ta sherikka yuborildi."

    async def cleanup_stale_games_on_startup(self) -> None:
        async with self.session_factory() as session:
            stale = (
                await session.execute(
                    select(Game).where(Game.status.in_([GameStatus.REGISTRATION.value, GameStatus.ACTIVE.value]))
                )
            ).scalars().all()
            for game in stale:
                game.status = GameStatus.CANCELLED.value
                game.phase = GamePhase.ENDED.value
                game.active_key = None
                game.ended_at = datetime.now(timezone.utc)
            await session.commit()

    async def registration_watchdog(self, bot: Bot) -> None:
        now = self._now_utc()
        async with self.session_factory() as session:
            games = (
                await session.execute(
                    select(Game).where(
                        Game.status == GameStatus.REGISTRATION.value,
                        Game.registration_ends_at.is_not(None),
                        Game.registration_ends_at <= now,
                    )
                )
            ).scalars().all()
        for game in games:
            await self.close_registration(bot, game.id)

    async def is_admin_or_creator(self, bot: Bot, chat_id: int, user_id: int, game_creator_id: Optional[int] = None) -> bool:
        if game_creator_id and user_id == game_creator_id:
            return True
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            return member.status in {"administrator", "creator"}
        except TelegramBadRequest:
            return False

    async def bot_is_admin(self, bot: Bot, chat_id: int) -> bool:
        me = await bot.get_me()
        try:
            member = await bot.get_chat_member(chat_id, me.id)
            return member.status in {"administrator", "creator"}
        except TelegramBadRequest:
            return False

    async def transfer_diamonds(self, from_user_id: int, to_user_id: int, amount: int) -> tuple[bool, str]:
        if amount <= 0:
            return False, "Miqdor musbat bo'lishi kerak."
        async with self.session_factory() as session:
            sender = (await session.execute(select(User).where(User.telegram_id == from_user_id))).scalar_one_or_none()
            receiver = (await session.execute(select(User).where(User.telegram_id == to_user_id))).scalar_one_or_none()
            if sender is None or receiver is None:
                return False, "Foydalanuvchi topilmadi."
            if sender.diamonds < amount:
                return False, "Balans yetarli emas."
            sender.diamonds -= amount
            receiver.diamonds += amount
            await session.commit()
            return True, "ok"

    @staticmethod
    def normalize_admin_username(raw: str) -> str:
        username = (raw or "").strip()
        username = username.removeprefix("https://t.me/").removeprefix("http://t.me/").removeprefix("t.me/")
        username = username.strip().lstrip("@").split("/", maxsplit=1)[0].strip()
        return username

    async def get_purchase_admin_username(self) -> str:
        async with self.session_factory() as session:
            setting = (
                await session.execute(select(BotSetting).where(BotSetting.key == "purchase_admin_username"))
            ).scalar_one_or_none()
            username = self.normalize_admin_username(setting.value if setting else self.settings.admin_username)
            return username or self.normalize_admin_username(self.settings.admin_username)

    async def set_purchase_admin_username(self, username: str) -> tuple[bool, str]:
        username = self.normalize_admin_username(username)
        if not username or len(username) < 5:
            return False, "Username noto'g'ri. Masalan: @username"
        async with self.session_factory() as session:
            setting = (
                await session.execute(select(BotSetting).where(BotSetting.key == "purchase_admin_username"))
            ).scalar_one_or_none()
            if setting is None:
                setting = BotSetting(key="purchase_admin_username", value=username)
                session.add(setting)
            else:
                setting.value = username
            await session.commit()
        return True, f"✅ Xarid admini yangilandi: @{username}"

    @staticmethod
    def normalize_telegram_url(raw: str) -> str:
        value = (raw or "").strip()
        if not value:
            return ""
        if value.startswith("@"):
            username = value.lstrip("@").strip()
            return f"https://t.me/{username}" if username else ""
        if value.startswith("t.me/"):
            path = value.removeprefix("t.me/").strip("/")
            return f"https://t.me/{path}" if path else ""
        if value.startswith("http://t.me/"):
            path = value.removeprefix("http://t.me/").strip("/")
            return f"https://t.me/{path}" if path else ""
        if value.startswith("https://t.me/"):
            path = value.removeprefix("https://t.me/").strip("/")
            return f"https://t.me/{path}" if path else ""
        return ""

    async def get_news_channel_url(self) -> Optional[str]:
        async with self.session_factory() as session:
            setting = (
                await session.execute(select(BotSetting).where(BotSetting.key == "news_channel_url"))
            ).scalar_one_or_none()
            raw_url = setting.value if setting else self.settings.news_channel_url
        return self.normalize_telegram_url(raw_url)

    async def set_news_channel_url(self, url: str) -> tuple[bool, str]:
        normalized = self.normalize_telegram_url(url)
        if not normalized:
            return False, "Link noto'g'ri. Masalan: @kanal yoki https://t.me/kanal"
        async with self.session_factory() as session:
            setting = (
                await session.execute(select(BotSetting).where(BotSetting.key == "news_channel_url"))
            ).scalar_one_or_none()
            if setting is None:
                setting = BotSetting(key="news_channel_url", value=normalized)
                session.add(setting)
            else:
                setting.value = normalized
            await session.commit()
        return True, f"✅ Yangiliklar kanali yangilandi:\n{normalized}"

    async def clear_news_channel_url(self) -> str:
        async with self.session_factory() as session:
            setting = (
                await session.execute(select(BotSetting).where(BotSetting.key == "news_channel_url"))
            ).scalar_one_or_none()
            if setting is None:
                setting = BotSetting(key="news_channel_url", value="")
                session.add(setting)
            else:
                setting.value = ""
            await session.commit()
        return "✅ Yangiliklar kanali o'chirildi. User paneldagi tugma endi ko'rinmaydi."

    async def exchange_diamonds_to_dollars(self, telegram_id: int, diamonds: Union[int, str]) -> tuple[bool, str]:
        async with self.session_factory() as session:
            user = (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
            if user is None:
                return False, "Avval /start bosing."

            if diamonds == "all":
                amount = int(user.diamonds or 0)
            else:
                amount = int(diamonds)

            if amount <= 0:
                return False, "Almashtirish uchun kamida 💎 1 almaz kerak."
            if (user.diamonds or 0) < amount:
                return False, f"Balans yetarli emas. Kerak: 💎 {amount}"

            dollars = amount * 500
            user.diamonds -= amount
            user.dollar += dollars
            await session.commit()

        return True, f"✅ 💎 {amount} almaz → 💵 {dollars} dollar almashtirildi."

    async def buy_shop_item(self, telegram_id: int, item_key: str) -> tuple[bool, str]:
        prices: dict[str, tuple[int, str, Union[int, str]]] = {
            "protection": (120, "protection", 1),
            "killer_protection": (100, "killer_protection", 1),
            "vote_protection": (80, "vote_protection", 1),
            "miner_protection": (90, "miner_protection", 1),
            "gun": (150, "gun", 1),
            "mask": (70, "mask", 1),
            "fake_document": (70, "fake_document", 1),
            "role:commissar": (300, "next_game_role", Role.COMMISSAR.value),
            "role:doctor": (260, "next_game_role", Role.DOCTOR.value),
            "role:don": (500, "next_game_role", Role.DON.value),
            "role:killer": (450, "next_game_role", Role.KILLER.value),
        }
        item = prices.get(item_key)
        if item is None:
            return False, "Bunday mahsulot topilmadi."
        price, field_name, value = item
        async with self.session_factory() as session:
            user = (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
            if user is None:
                return False, "Avval /start bosing."
            if user.dollar < price:
                return False, f"Balans yetarli emas. Kerak: 💵 {price}"
            user.dollar -= price
            if field_name == "next_game_role":
                user.next_game_role = str(value)
            else:
                current = int(getattr(user, field_name) or 0)
                setattr(user, field_name, current + int(value))
            await session.commit()
        return True, "✅ Xarid muvaffaqiyatli amalga oshirildi."

    async def owner_stats(self) -> str:
        async with self.session_factory() as session:
            users_count = await session.scalar(select(func.count(User.id)))
            groups_count = await session.scalar(select(func.count(Group.id)))
            active_games = await session.scalar(
                select(func.count(Game.id)).where(Game.status.in_([GameStatus.REGISTRATION.value, GameStatus.ACTIVE.value]))
            )
            completed_games = await session.scalar(select(func.count(Game.id)).where(Game.status == GameStatus.COMPLETED.value))
        return (
            "📊 <b>Bot statistikasi</b>\n\n"
            f"👤 Userlar: <b>{users_count or 0}</b>\n"
            f"🏘 Guruhlar: <b>{groups_count or 0}</b>\n"
            f"🎮 Aktiv o'yinlar: <b>{active_games or 0}</b>\n"
            f"✅ Tugagan o'yinlar: <b>{completed_games or 0}</b>"
        )

    async def broadcast(self, bot: Bot, target: str, text: str) -> tuple[int, int]:
        if target not in {"users", "groups"}:
            return 0, 0
        async with self.session_factory() as session:
            if target == "users":
                ids = (await session.execute(select(User.telegram_id))).scalars().all()
            else:
                ids = (await session.execute(select(Group.chat_id))).scalars().all()

        sent = 0
        failed = 0
        for chat_id in ids:
            try:
                await bot.send_message(chat_id, text)
                sent += 1
                await asyncio.sleep(0.04)
            except (TelegramBadRequest, TelegramForbiddenError):
                failed += 1
        return sent, failed

    async def broadcast_message(
        self,
        bot: Bot,
        target: str,
        from_chat_id: int,
        message_id: int,
    ) -> tuple[int, int]:
        if target not in {"users", "groups"}:
            return 0, 0
        async with self.session_factory() as session:
            if target == "users":
                ids = (await session.execute(select(User.telegram_id))).scalars().all()
            else:
                ids = (await session.execute(select(Group.chat_id))).scalars().all()

        sent = 0
        failed = 0
        for chat_id in ids:
            try:
                await bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                )
                sent += 1
                await asyncio.sleep(0.04)
            except (TelegramBadRequest, TelegramForbiddenError):
                failed += 1
        return sent, failed

    async def grant_balance(
        self,
        telegram_id: int,
        dollar: int = 0,
        diamonds: int = 0,
    ) -> tuple[bool, str]:
        async with self.session_factory() as session:
            user = (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
            if user is None:
                return False, "User topilmadi. U avval /start qilgan bo'lishi kerak."
            user.dollar += dollar
            user.diamonds += diamonds
            await session.commit()
        return True, f"✅ Berildi: 💵 {dollar}, 💎 {diamonds}"

    async def add_premium_group(
        self,
        title: str,
        invite_link: str,
        diamond_price: int,
        created_by: int,
    ) -> PremiumGroup:
        async with self.session_factory() as session:
            group = PremiumGroup(
                title=title.strip()[:255],
                invite_link=invite_link.strip(),
                diamond_price=max(0, diamond_price),
                created_by=created_by,
                is_active=True,
            )
            session.add(group)
            await session.commit()
            await session.refresh(group)
            return group

    async def premium_groups(self, include_inactive: bool = False) -> list[PremiumGroup]:
        async with self.session_factory() as session:
            stmt = select(PremiumGroup).order_by(PremiumGroup.total_diamonds.desc(), PremiumGroup.id.desc())
            if not include_inactive:
                stmt = stmt.where(
                    PremiumGroup.is_active.is_(True),
                    PremiumGroup.total_diamonds > 0,
                )
            return (await session.execute(stmt)).scalars().all()

    async def premium_groups_text(self, include_inactive: bool = False) -> str:
        groups = await self.premium_groups(include_inactive=include_inactive)
        if not groups:
            return (
                "🎲 <b>Premium guruhlar</b>\n\n"
                "Hozircha guruhlar 💎 almaz yubormagan.\n"
                "Guruhda <code>/gsend miqdor</code> yozib reytingga chiqish mumkin."
            )
        return "🎲 <b>Premium guruhlar</b>\n\nKerakli guruhni tanlang:"

    async def owner_premium_groups_manage_text(self) -> str:
        groups = await self.premium_groups(include_inactive=True)
        if not groups:
            return "🎲 <b>Premium guruhlar boshqaruvi</b>\n\nHozircha ro'yxatda guruh yo'q."
        lines = ["🎲 <b>Premium guruhlar boshqaruvi</b>", "", "Bankrot qilish uchun guruh tugmasini bosing:", ""]
        for group in groups:
            status = "aktiv" if group.is_active and (group.total_diamonds or 0) > 0 else "bankrot"
            lines.append(f"<b>{group.title}</b> | 💎 {group.total_diamonds or 0} | {status}")
        return "\n".join(lines)

    async def premium_blocked_users_text(self) -> str:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(PremiumBlockedUser).order_by(PremiumBlockedUser.created_at.desc()).limit(50)
                )
            ).scalars().all()
        if not rows:
            return "🚷 <b>Bloklangan userlar</b>\n\nHozircha bloklangan user yo'q."
        lines = ["🚷 <b>Bloklangan userlar</b>\n"]
        for idx, row in enumerate(rows, 1):
            reason = f" | {escape(row.reason)}" if row.reason else ""
            lines.append(f"{idx}. {self._tg_mention(row.telegram_id, row.display_name)} - <code>{row.telegram_id}</code>{reason}")
        return "\n".join(lines)

    async def bankrupt_premium_group(self, raw_group_id: str) -> tuple[bool, str]:
        raw_group_id = raw_group_id.strip()
        if not raw_group_id.isdigit():
            return False, "Guruh ID raqam bo'lishi kerak. Ro'yxatdan IDni yuboring."
        group_id = int(raw_group_id)
        async with self.session_factory() as session:
            group = (
                await session.execute(select(PremiumGroup).where(PremiumGroup.id == group_id))
            ).scalar_one_or_none()
            if group is None:
                return False, "Bunday premium guruh topilmadi."
            title = group.title
            group.total_diamonds = 0
            group.diamond_price = 0
            group.top_sender_telegram_id = None
            group.top_sender_name = None
            group.top_sender_diamonds = 0
            group.is_active = False
            contributions = (
                await session.execute(
                    select(PremiumGroupContribution).where(PremiumGroupContribution.premium_group_id == group.id)
                )
            ).scalars().all()
            for contribution in contributions:
                await session.delete(contribution)
            await session.commit()
        return True, f"🧨 <b>{escape(title)}</b> bankrot qilindi va premium ro'yxatdan olib tashlandi."

    async def bankrupt_premium_group_by_chat(self, chat_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            group = (
                await session.execute(select(PremiumGroup).where(PremiumGroup.group_chat_id == chat_id))
            ).scalar_one_or_none()
            if group is None or (group.total_diamonds or 0) <= 0:
                return False, "Bu guruh premium ro'yxatda topilmadi."
            group_id = group.id
        return await self.bankrupt_premium_group(str(group_id))

    async def _find_user_by_identifier(self, session: AsyncSession, raw_identifier: str) -> Optional[User]:
        identifier = raw_identifier.strip()
        if not identifier:
            return None
        if identifier.lstrip("-").isdigit():
            return (
                await session.execute(select(User).where(User.telegram_id == int(identifier)))
            ).scalar_one_or_none()
        username = self.normalize_admin_username(identifier).lower()
        if not username:
            return None
        users = (
            await session.execute(select(User).where(User.username.is_not(None)))
        ).scalars().all()
        return next((user for user in users if (user.username or "").lower().lstrip("@") == username), None)

    async def block_premium_user(self, raw: str, blocked_by: int) -> tuple[bool, str]:
        parts = raw.strip().split(maxsplit=1)
        if not parts:
            return False, "User ID yoki username yuboring. Masalan: <code>@username reklama</code>"
        reason = parts[1].strip() if len(parts) > 1 else None
        async with self.session_factory() as session:
            user = await self._find_user_by_identifier(session, parts[0])
            if user is None:
                return False, "User topilmadi. U avval botda /start qilgan bo'lishi kerak."
            telegram_id = user.telegram_id
            display_name = user.display_name
            row = (
                await session.execute(select(PremiumBlockedUser).where(PremiumBlockedUser.telegram_id == telegram_id))
            ).scalar_one_or_none()
            if row is None:
                row = PremiumBlockedUser(
                    telegram_id=telegram_id,
                    display_name=display_name,
                    reason=reason,
                    blocked_by=blocked_by,
                )
                session.add(row)
            else:
                row.display_name = display_name
                row.reason = reason
                row.blocked_by = blocked_by
            await session.commit()
        return True, f"🚫 User bloklandi: {self._tg_mention(telegram_id, display_name)}"

    async def unblock_premium_user(self, raw: str) -> tuple[bool, str]:
        raw = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if not raw:
            return False, "Blokdan chiqarish uchun user ID yoki username yuboring."
        async with self.session_factory() as session:
            user = await self._find_user_by_identifier(session, raw)
            telegram_id = user.telegram_id if user else int(raw) if raw.lstrip("-").isdigit() else None
            if telegram_id is None:
                return False, "User topilmadi. ID yoki username'ni tekshiring."
            row = (
                await session.execute(select(PremiumBlockedUser).where(PremiumBlockedUser.telegram_id == telegram_id))
            ).scalar_one_or_none()
            if row is None:
                return False, "Bu user bloklanganlar ro'yxatida yo'q."
            await session.delete(row)
            await session.commit()
        return True, f"✅ User blokdan chiqarildi: <code>{telegram_id}</code>"

    async def is_premium_user_blocked(self, telegram_id: int) -> bool:
        async with self.session_factory() as session:
            row = (
                await session.execute(select(PremiumBlockedUser.telegram_id).where(PremiumBlockedUser.telegram_id == telegram_id))
            ).scalar_one_or_none()
            return row is not None

    async def contribute_premium_group(
        self,
        bot: Bot,
        chat_id: int,
        chat_title: str,
        tg_user: TgUser,
        diamonds: int,
    ) -> tuple[bool, str]:
        if diamonds <= 0:
            return False, "Miqdor musbat bo'lishi kerak. Masalan: /gsend 10"
        if await self.is_premium_user_blocked(tg_user.id):
            return False, "Siz premium guruh reytingiga almaz yuborishdan bloklangansiz."

        user = await self.ensure_user(tg_user)
        invite_link = await self.group_return_url(bot, chat_id)
        async with self.session_factory() as session:
            fresh_user = (
                await session.execute(select(User).where(User.telegram_id == user.telegram_id))
            ).scalar_one_or_none()
            if fresh_user is None:
                return False, "Avval /start bosing."
            if (fresh_user.diamonds or 0) < diamonds:
                return False, f"Balans yetarli emas. Kerak: 💎 {diamonds}"

            group = (
                await session.execute(
                    select(PremiumGroup).where(PremiumGroup.group_chat_id == chat_id)
                )
            ).scalar_one_or_none()
            if group is None:
                group = PremiumGroup(
                    title=(chat_title or "Group")[:255],
                    invite_link=invite_link,
                    diamond_price=0,
                    total_diamonds=0,
                    group_chat_id=chat_id,
                    created_by=tg_user.id,
                    is_active=True,
                )
                session.add(group)
                await session.flush()
            else:
                group.title = (chat_title or group.title or "Group")[:255]
                group.invite_link = invite_link
                group.is_active = True

            contribution = (
                await session.execute(
                    select(PremiumGroupContribution).where(
                        PremiumGroupContribution.premium_group_id == group.id,
                        PremiumGroupContribution.user_telegram_id == tg_user.id,
                    )
                )
            ).scalar_one_or_none()
            if contribution is None:
                contribution = PremiumGroupContribution(
                    premium_group_id=group.id,
                    user_telegram_id=tg_user.id,
                    user_name=fresh_user.display_name,
                    diamonds=0,
                )
                session.add(contribution)

            fresh_user.diamonds -= diamonds
            group.total_diamonds = int(group.total_diamonds or 0) + diamonds
            group.diamond_price = group.total_diamonds
            contribution.user_name = fresh_user.display_name
            contribution.diamonds = int(contribution.diamonds or 0) + diamonds

            top = (
                await session.execute(
                    select(PremiumGroupContribution).where(
                        PremiumGroupContribution.premium_group_id == group.id
                    )
                )
            ).scalars().all()
            top_contributor = max(top, key=lambda item: item.diamonds, default=contribution)
            if contribution.diamonds >= top_contributor.diamonds:
                top_contributor = contribution
            group.top_sender_telegram_id = top_contributor.user_telegram_id
            group.top_sender_name = top_contributor.user_name
            group.top_sender_diamonds = top_contributor.diamonds

            await session.commit()
            total = group.total_diamonds
            user_total = contribution.diamonds

        return (
            True,
            f"✅ {self._tg_mention(tg_user.id, user.display_name)} guruh reytingi uchun 💎 {diamonds} almaz yubordi.\n"
            f"🎲 Guruh jami: 💎 {total}\n"
            f"👤 Siz yuborgan jami: 💎 {user_total}",
        )

    async def buy_premium_group(self, telegram_id: int, premium_group_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            group = (
                await session.execute(
                    select(PremiumGroup).where(
                        PremiumGroup.id == premium_group_id,
                        PremiumGroup.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if group is None:
                return False, "Premium guruh topilmadi yoki o'chirilgan."
            return (
                True,
                f"🎲 <b>{group.title}</b>\n\n"
                f"💎 Kirish narxi: <b>{group.diamond_price}</b>\n"
                f"🔗 Guruh linki: {group.invite_link}",
            )

    async def top_players(self, limit: int = 10) -> list[User]:
        async with self.session_factory() as session:
            return (
                await session.execute(select(User).order_by(User.wins.desc(), User.total_games.desc()).limit(limit))
            ).scalars().all()

    async def top_players_in_group(self, chat_id: int, limit: int = 10) -> list[tuple[str, int, int]]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        GamePlayer.display_name,
                        func.sum(case((GamePlayer.won.is_(True), 1), else_=0)).label("wins"),
                        func.count(GamePlayer.id).label("total"),
                    )
                    .join(Game, Game.id == GamePlayer.game_id)
                    .where(Game.chat_id == chat_id, Game.status == GameStatus.COMPLETED.value)
                    .group_by(GamePlayer.telegram_id, GamePlayer.display_name)
                    .order_by(func.sum(case((GamePlayer.won.is_(True), 1), else_=0)).desc(), func.count(GamePlayer.id).desc())
                    .limit(limit)
                )
            ).all()
            return [(name, int(wins or 0), int(total or 0)) for name, wins, total in rows]

    async def group_settings(self, chat_id: int) -> Group:
        group = await self.get_or_create_group(chat_id, "Group")
        return group

    async def latest_group_game_logs_text(self, chat_id: int, limit: int = 15) -> str:
        async with self.session_factory() as session:
            game = (
                await session.execute(
                    select(Game)
                    .where(Game.chat_id == chat_id)
                    .order_by(Game.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if game is None:
                return "🧾 <b>Game logs</b>\n\nBu guruhda hali o'yin topilmadi."

            logs = (
                await session.execute(
                    select(GameLog)
                    .where(GameLog.game_id == game.id)
                    .order_by(GameLog.id.desc())
                    .limit(max(1, min(limit, 30)))
                )
            ).scalars().all()

        if not logs:
            return f"🧾 <b>Game logs</b>\n\nO'yin #{game.id} uchun hali log yozilmagan."

        lines = [
            "🧾 <b>Game logs</b>",
            f"O'yin: <b>#{game.id}</b> | Holat: <b>{game.status}</b> | Faza: <b>{game.phase}</b>",
            "",
        ]
        for item in reversed(logs):
            actor_name = "-"
            target_name = "-"
            try:
                payload = json.loads(item.payload or "{}")
                actor = payload.get("actor") or {}
                target = payload.get("target") or {}
                actor_name = actor.get("display_name") or "-"
                target_name = target.get("display_name") or "-"
                day = payload.get("day_number", 0)
                night = payload.get("night_number", 0)
            except (TypeError, ValueError, AttributeError):
                day = 0
                night = 0
            created = item.created_at.strftime("%H:%M") if item.created_at else "--:--"
            lines.append(
                f"{created} | <code>{item.event_type}</code> | D:{day} N:{night} | {escape(str(actor_name))} -> {escape(str(target_name))}"
            )
        return "\n".join(lines)

    async def update_group_setting(self, chat_id: int, field: str, value: object) -> None:
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                group = Group(chat_id=chat_id, title="Group")
                session.add(group)
            if field == "registration_timeout":
                group.registration_timeout = max(10, int(value))
            elif field == "min_players":
                group.min_players = max(4, min(int(value), 30))
            elif field == "role_preset":
                preset = str(value)
                if preset in {"black23", "extended35"}:
                    group.role_preset = preset
            await session.commit()

    def format_role_preset_settings(self, group: Group) -> str:
        preset = group.role_preset or "black23"
        return (
            "🎭 <b>Role settings</b>\n\n"
            f"Joriy preset: <b>{role_preset_label(preset)}</b>\n"
            f"Maksimal tavsiya qilingan o'yinchi: <b>{role_preset_max_players(preset)}</b>\n\n"
            "<b>Universal 30</b> - 4 dan 30 tagacha o'yinchi uchun siz bergan role jadvali bo'yicha taqsimlaydi."
        )
