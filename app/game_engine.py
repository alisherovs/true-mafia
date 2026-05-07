from __future__ import annotations

from typing import Optional, Union
import logging
import asyncio
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
    go_vote_private_keyboard,
    go_group_keyboard,
    group_url_from_chat_id,
    judge_cancel_keyboard,
    lobby_keyboard,
    profile_dashboard_keyboard,
    target_keyboard,
    vote_keyboard,
)
from app.models import Game, GameLog, GamePlayer, Group, HangVote, NightAction, PremiumGroup, User, Vote
from app.roles import ROLE_META, build_role_set, role_label, role_team
from app.scheduler import scheduler
from app.texts import t

logger = logging.getLogger(__name__)


class GameEngine:
    def __init__(self, settings: Settings, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.settings = settings
        self.session_factory = session_factory

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
        if name:
            return name[:255]
        if tg_user.username:
            return tg_user.username[:255]
        return f"Player {tg_user.id}"

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
        roles = ", ".join(role_label(player.role) for player in players)
        return f"{title} - {count}\n{roles}"

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
        return f"{base} vaxshiylarcha o'ldirildi..."

    def _build_day_result_text(
        self,
        day_number: int,
        alive_players: list[GamePlayer],
        dead_players: list[GamePlayer],
        transformed: list[str],
        night_activity_lines: list[str],
        night_event_lines: list[str],
        death_causes: Optional[dict[int, str]] = None,
    ) -> str:
        death_causes = death_causes or {}
        if dead_players:
            night_result = "\n".join(
                self._format_death_line(player, death_causes.get(player.telegram_id))
                for player in dead_players
            )
        else:
            night_result = "Ishonish qiyin, lekin bu tunda hech kim o'lmadi..."

        city_players = [player for player in alive_players if player.team == Team.CITY.value]
        mafia_players = [player for player in alive_players if player.team == Team.MAFIA.value]
        killer_players = [player for player in alive_players if player.team == Team.KILLER.value]
        neutral_players = [player for player in alive_players if player.team == Team.NEUTRAL.value]

        group_blocks = [
            self._format_role_group("🏘 <b>Tinch aholilar</b>", len(city_players), city_players),
            self._format_role_group("🤵🏻 <b>Mafiya</b>", len(mafia_players), mafia_players),
            self._format_role_group("🔪 <b>Yolg'iz xavf</b>", len(killer_players), killer_players),
            self._format_role_group("🎭 <b>Neytral</b>", len(neutral_players), neutral_players),
        ]
        groups_text = "\n\n".join(block for block in group_blocks if block)
        transform_text = ""
        if transformed:
            transform_text = "\n\n" + "\n".join(f"🔁 {line}" for line in transformed)
        activity_text = ""
        if night_activity_lines:
            activity_text = "\n\n" + "\n\n".join(night_activity_lines)
        events_text = ""
        if night_event_lines:
            events_text = "\n\n" + "\n".join(night_event_lines)
        night_story = f"{activity_text}\n\n{night_result}{events_text}{transform_text}".strip()

        return (
            "Xayrli tong🌝 \n"
            f"🌄<b>Kun: {day_number}</b>\n"
            "Shamollar tundagi mish-mishlarni butun shaharga yetkazmoqda..\n\n\n"
            f"{night_story}\n\n\n"
            "Tirik o'yinchilar: \n"
            f"{self._format_alive_players(alive_players)}\n\n"
            f"{groups_text}\n\n"
            f"<b>Jami:</b> {len(alive_players)}\n\n"
            "Endi kechaning natijalarini muhokama qilish, sabablari va oqibatlarini tushunish vaqti keldi ..."
        )

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
        if role == Role.COMMISSAR:
            if action_key == "shoot":
                return "🕵🏼 Komissar katani shubhali odamni nishonga oldi..."
            return "🕵🏼 Komissar Katani yovuzlarni qidirishga ketdi..."
        if role == Role.LAWYER:
            return "👨🏼‍💼 Advokat Mafiani ximoya qilish uchun qidiryapti..."
        if role == Role.KILLER:
            return "🕴️ Убийца выбрал свою жертву!"
        if role == Role.BUM:
            return "🧙🏼 Daydi kimnikigadir ichkilik butilka olish uchun ketdi..."
        return None

    def _role_fall_line(self, player: GamePlayer) -> str:
        name = self._tg_mention(player.telegram_id, player.display_name)
        role = Role(player.role)
        if role == Role.CITIZEN:
            return f"👨🏼 {name} shaharning oddiy, ammo muhim ovozi edi..."
        if role == Role.DOCTOR:
            return f"👨🏼‍⚕️️ {name} endi hech kimni davolay olmaydi..."
        if role == Role.GUARD:
            return f"🛡 {name} himoya qalqonini yerga tashladi..."
        if role == Role.WATCHER:
            return f"🔎 {name}ning kuzatuvlari shu yerda tugadi..."
        if role == Role.JUDGE:
            return f"🧑‍⚖️ {name}ning oxirgi hukmi aytilmay qoldi..."
        if role == Role.MISTRESS:
            return f"💃 {name}ning sirli mehmonlari endi kelmaydi..."
        if role == Role.SERGEANT:
            return f"👮🏻‍♂ {name} Komissar Katani yo'lidagi sodiq yordamchi edi..."
        if role == Role.COMMISSAR:
            return f"🕵🏼 {name} yovuzlarni qidirayotgan izquvar edi..."
        if role == Role.BUM:
            return f"🧙🏼 {name} oxirgi bor tun ko'chalarida iz qoldirdi..."
        if role == Role.SORCERER:
            return f"🧙‍ Sehrgar {name} o'limida ham sehrini yo'qotmadi..."
        if role == Role.DON:
            return f"🤵🏻 Don {name} yiqildi, lekin mafiya soyasi hali tarqalmadi..."
        if role == Role.MAFIA:
            return f"🤵🏼 {name} mafiya oilasining jim askarlaridan biri edi..."
        if role == Role.LAWYER:
            return f"🤹🏻 Aferist {name} endi hech kimning izini yashira olmaydi..."
        if role == Role.SPY:
            return f"🕴 Josus {name} nihoyat fosh bo'ldi..."
        if role == Role.KILLER:
            return f"🔪 Qotil {name}ning pichog'i nihoyat jim qoldi..."
        if role == Role.WOLF:
            return f"🐺 {name} o'z qiyofasini topishga ulgurmay ketdi..."
        if role == Role.JESTER:
            return f"🎭 Masxaraboz {name} sahnadan tushdi..."
        return f"{name} tun qurboni bo'ldi..."

    def _last_words_line(self, player: GamePlayer, words: str) -> str:
        safe_words = escape(words.strip()[:500])
        name = self._tg_mention(player.telegram_id, player.display_name)
        return f"O'limidan oldin {name} qichqirganini eshitdi:\n{safe_words}"

    def _sleep_death_line(self, player: GamePlayer) -> str:
        name = self._tg_mention(player.telegram_id, player.display_name)
        return (
            f"O'limidan oldin {name} qichqirganini eshitdi:\n"
            "Men o'yin paytida boshqa uxlamayma-a-a-a-a-a-an!"
        )

    def _apply_role_successions(self, alive_players: list[GamePlayer], dead_ids: set[int]) -> list[str]:
        lines: list[str] = []
        dead_players = [player for player in alive_players if player.telegram_id in dead_ids]

        if any(Role(player.role) == Role.DON for player in dead_players):
            heir = next(
                (
                    player
                    for player in alive_players
                    if player.telegram_id not in dead_ids and Role(player.role) in {Role.MAFIA, Role.SPY}
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

    async def get_user_language(self, telegram_id: int) -> str:
        user = await self.get_user(telegram_id)
        if user and user.language:
            return user.language
        return self.settings.default_language

    async def get_group_language(self, chat_id: int) -> str:
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            return group.language if group else self.settings.default_language

    async def group_return_url(self, bot: Bot, chat_id: int) -> str:
        try:
            chat = await bot.get_chat(chat_id)
            username = getattr(chat, "username", None)
            if username:
                return f"https://t.me/{username}"

            invite_link = getattr(chat, "invite_link", None)
            if invite_link:
                return invite_link

            try:
                return await bot.export_chat_invite_link(chat_id)
            except (TelegramBadRequest, TelegramForbiddenError):
                pass
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

        return group_url_from_chat_id(chat_id)

    async def group_return_keyboard(self, bot: Bot, chat_id: int):
        return go_group_keyboard(chat_id, await self.group_return_url(bot, chat_id))

    async def log(self, game_id: int, event_type: str, payload: str) -> None:
        async with self.session_factory() as session:
            session.add(GameLog(game_id=game_id, event_type=event_type, payload=payload))
            await session.commit()

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
            await session.commit()

        try:
            await bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
        except TelegramBadRequest:
            await bot.send_message(chat_id, "⚠️ Pin qilishga ruxsat yo'q, lekin o'yin davom etadi.")

        try:
            await self.schedule_registration_jobs(bot, game.id)
        except Exception:
            logger.exception("Failed to schedule registration jobs for game_id=%s", game.id)
        await self.log(game.id, LogType.GAME_EVENT.value, "Registration started")
        return True, t(await self.get_group_language(chat_id), "registration_started")

    async def _build_lobby_text(self, session: AsyncSession, game_id: int, lang: str, ended: bool) -> str:
        players = (
            await session.execute(
                select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.id.asc())
            )
        ).scalars().all()
        names = ", ".join(self._tg_mention(p.telegram_id, p.display_name) for p in players) if players else t(lang, "lobby_empty")
        title_key = "lobby_ended_title" if ended else "lobby_title"
        return (
            f"{t(lang, title_key)}\n\n"
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
        user = await self.ensure_user(tg_user)
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None:
                return False, t(self.settings.default_language, "callback_expired")
            lang = await self.get_group_language(game.chat_id)
            if game.status != GameStatus.REGISTRATION.value:
                return False, t(lang, "registration_closed_cb")

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
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False, t(lang, "already_joined")

        await self.update_lobby(bot, game_id)
        return True, t(await self.get_user_language(tg_user.id), "joined")

    async def join_game_by_deeplink(
        self,
        bot: Bot,
        game_id: int,
        chat_id: int,
        tg_user: TgUser,
    ) -> tuple[bool, str]:
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
            await session.delete(player)
            await session.commit()

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
            await session.commit()
            lang = await self.get_group_language(game.chat_id)

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
                await session.commit()
                await self.update_lobby(bot, game_id, ended=True)
                await bot.send_message(game.chat_id, t(lang, "insufficient_players"))
                self._cleanup_jobs(game_id)
                return

            game.status = GameStatus.ACTIVE.value
            game.phase = GamePhase.NIGHT.value
            game.started_at = datetime.now(timezone.utc)
            game.night_number = 1
            await session.commit()

        await self.update_lobby(bot, game_id, ended=True)
        await bot.send_message((await self._game_chat_id(game_id)), t(lang, "registration_ended"))
        await self.assign_roles_and_notify(bot, game_id)
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
            roles = build_role_set(len(players))
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
            await session.commit()

            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one()
            lang = await self.get_group_language(game.chat_id)

        for player in players:
            role = Role(player.role)
            try:
                text = (
                    f"🎭 <b>Sizning rolingiz:</b> {role_label(role)}\n"
                    f"{ROLE_META[role].short_desc_uz}\n"
                    f"\nO'yin chatiga qayting va strategiyani boshlang."
                )
                await bot.send_message(player.telegram_id, text)
            except TelegramForbiddenError:
                await bot.send_message(game.chat_id, f"{player.display_name}: {t(lang, 'need_start_for_role')}")

    async def _send_phase_media(self, bot: Bot, chat_id: int, is_night: bool, lang: str) -> None:
        caption = t(lang, "night_title") if is_night else t(lang, "day_title")
        kb = go_private_keyboard(self.settings)
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
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value:
                return
            game.phase = GamePhase.NIGHT.value
            await session.commit()
            chat_id = game.chat_id
            lang = await self.get_group_language(chat_id)

        await self._send_phase_media(bot, chat_id, is_night=True, lang=lang)
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

    async def send_night_prompts(self, bot: Bot, game_id: int) -> None:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one()
            night = game.night_number
            alive = await self._alive_players(session, game_id)

        all_choices = [(p.telegram_id, p.display_name) for p in alive]

        for player in alive:
            role = Role(player.role)
            targets = [(tid, name) for tid, name in all_choices if tid != player.telegram_id]

            try:
                if role in {Role.MAFIA, Role.DON, Role.SPY}:
                    await bot.send_message(
                        player.telegram_id,
                        "🌚 Kimni yo'q qilamiz?",
                        reply_markup=target_keyboard("kill", game_id, player.telegram_id, targets),
                    )
                elif role == Role.DOCTOR:
                    await bot.send_message(
                        player.telegram_id,
                        "💉 Kimni davolaysiz? (o'zingizni faqat 1 marta)",
                        reply_markup=target_keyboard("heal", game_id, player.telegram_id, all_choices),
                    )
                elif role == Role.GUARD:
                    await bot.send_message(
                        player.telegram_id,
                        "🛡 Kimni tunda himoya qilasiz?",
                        reply_markup=target_keyboard("guard", game_id, player.telegram_id, all_choices),
                    )
                elif role == Role.WATCHER:
                    await bot.send_message(
                        player.telegram_id,
                        "🔎 Kimni kuzatasiz? Unga kim kelganini bilasiz.",
                        reply_markup=target_keyboard("watch", game_id, player.telegram_id, targets),
                    )
                elif role == Role.COMMISSAR:
                    await bot.send_message(
                        player.telegram_id,
                        "🕵🏼 <b>Komissar Katani navbati</b>\n\n"
                        "Bugun tunda qanday harakat qilasiz?",
                        reply_markup=commissar_action_keyboard(
                            game_id=game_id,
                            actor_id=player.telegram_id,
                            can_shoot=night >= 2,
                        ),
                    )
                elif role == Role.MISTRESS:
                    await bot.send_message(
                        player.telegram_id,
                        "💃 Kimni harakatdan to'xtatasiz?",
                        reply_markup=target_keyboard("block", game_id, player.telegram_id, targets),
                    )
                elif role == Role.LAWYER:
                    await bot.send_message(
                        player.telegram_id,
                        "👨‍💼 Kimni himoya qilasiz?",
                        reply_markup=target_keyboard("defend", game_id, player.telegram_id, targets),
                    )
                elif role == Role.KILLER:
                    await bot.send_message(
                        player.telegram_id,
                        "🔪 Kimni o'ldirasiz?",
                        reply_markup=target_keyboard("killer", game_id, player.telegram_id, targets),
                    )
                elif role == Role.BUM:
                    await bot.send_message(
                        player.telegram_id,
                        "🧙‍♂ Kimni kuzatasiz?",
                        reply_markup=target_keyboard("visit", game_id, player.telegram_id, targets),
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
        }
        action_type = action_map.get(action_key)
        if action_type is None:
            return False, "Unknown action"

        announce_line: Optional[str] = None
        chat_id: Optional[int] = None
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
                Role.DOCTOR: {"heal"},
                Role.GUARD: {"guard"},
                Role.WATCHER: {"watch"},
                Role.COMMISSAR: {"check", "shoot"},
                Role.MISTRESS: {"block"},
                Role.LAWYER: {"defend"},
                Role.KILLER: {"killer"},
                Role.BUM: {"visit"},
                Role.SORCERER: {"revenge"},
            }
            role_allowed = allowed_actions.get(actor_role, set())
            if action_key not in role_allowed:
                return False, "Bu amal sizning rolingiz uchun mavjud emas."
            if actor_role == Role.COMMISSAR and action_key == "shoot" and game.night_number < 2:
                return False, "Komissar birinchi tunda tekshirmasdan ota olmaydi."

            if action_type == ActionType.HEAL and target_id == actor_id and actor.self_heal_used:
                return False, "Siz o'zingizni yana davolay olmaysiz."

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
            if actor_role == Role.MISTRESS and Role(target.role) == Role.COMMISSAR:
                return False, "Kezuvchi Komissarni qasddan uxlatmasligi kerak."

            existing = (
                await session.execute(
                    select(NightAction).where(
                        NightAction.game_id == game_id,
                        NightAction.night_number == game.night_number,
                        NightAction.actor_telegram_id == actor_id,
                        NightAction.action_type == action_type.value,
                    )
                )
            ).scalar_one_or_none()
            should_announce = True
            if existing is not None:
                should_announce = existing.target_telegram_id != target_id or existing.details != action_key
                existing.target_telegram_id = target_id
                existing.details = action_key
            else:
                session.add(
                    NightAction(
                        game_id=game_id,
                        night_number=game.night_number,
                        actor_telegram_id=actor_id,
                        target_telegram_id=target_id,
                        action_type=action_type.value,
                        details=action_key,
                    )
                )

            if action_type == ActionType.HEAL and actor.telegram_id == target_id:
                actor.self_heal_used = True

            if should_announce:
                announce_line = self._night_activity_line(actor_role, action_key)

            await session.commit()

        if announce_line and chat_id is not None:
            try:
                await bot.send_message(chat_id, announce_line)
            except TelegramBadRequest:
                logger.warning("Unable to announce night action for game %s", game_id)

        return True, t(self.settings.default_language, "action_saved")

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
            if action_key == "shoot" and game.night_number < 2:
                return False, "Komissar birinchi tunda ota olmaydi.", None
            alive = await self._alive_players(session, game_id)

        choices = [(p.telegram_id, p.display_name) for p in alive if p.telegram_id != actor_id]
        title = "🕵️ Kimni tekshiramiz?" if action_key == "check" else "🔫 Kimni otamiz?"
        return True, title, target_keyboard(action_key, game_id, actor_id, choices)

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
                elif act.action_type == ActionType.DEFEND.value and act.target_telegram_id:
                    defended.add(act.target_telegram_id)
                elif act.action_type == ActionType.GUARD.value and act.target_telegram_id:
                    guarded.add(act.target_telegram_id)
                elif act.action_type == ActionType.WATCH.value and act.target_telegram_id:
                    watched[act.actor_telegram_id] = act.target_telegram_id
                elif act.action_type == ActionType.VISIT.value and act.target_telegram_id:
                    visited[act.actor_telegram_id] = act.target_telegram_id

                if (
                    act.target_telegram_id
                    and act.actor_telegram_id != act.target_telegram_id
                    and act.action_type != ActionType.WATCH.value
                ):
                    visitors_by_target[act.target_telegram_id].append(act.actor_telegram_id)

            mafia_kills = []
            killer_kills = []
            commissar_shots = []
            checks = []
            night_activity_lines: list[str] = []

            for act in actions:
                if act.actor_telegram_id in blocked:
                    continue
                actor = player_map.get(act.actor_telegram_id)
                if actor is None:
                    continue
                role = Role(actor.role)
                activity_line = self._night_activity_line(role, act.details)
                if activity_line:
                    night_activity_lines.append(activity_line)
                target_id = act.target_telegram_id
                if target_id is None:
                    continue

                if act.action_type == ActionType.KILL.value and role in {Role.DON, Role.MAFIA, Role.SPY}:
                    mafia_kills.append(target_id)
                elif act.action_type == ActionType.KILL.value and role == Role.KILLER:
                    killer_kills.append(target_id)
                elif act.action_type == ActionType.SHOOT.value and role == Role.COMMISSAR:
                    commissar_shots.append(target_id)
                elif act.action_type == ActionType.CHECK.value and role == Role.COMMISSAR:
                    checks.append((act.actor_telegram_id, target_id))

            dead: set[int] = set()
            death_causes: dict[int, str] = {}
            mafia_dead: set[int] = set()
            transformed: list[str] = []
            night_event_lines: list[str] = []
            last_words_prompts: list[tuple[int, str]] = []

            mafia_target = Counter(mafia_kills).most_common(1)
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
                        mafia_dead.add(target)
                        night_event_lines.append(
                            f"🤵🏻 Mafiya {self._tg_mention(target_player.telegram_id, target_player.display_name)} iziga tushdi..."
                        )
                    elif target in guarded:
                        night_event_lines.append("🛡 Qo'riqchi tunda kimningdir hayotini to'sib qoldi...")
                    else:
                        night_event_lines.append(
                            f"👨🏼‍⚕️️Doktor kimningdir hayotini saqlab qoldi..."
                        )

            killer_target = Counter(killer_kills).most_common(1)
            if killer_target:
                target = killer_target[0][0]
                target_player = player_map.get(target)
                if target_player:
                    if Role(target_player.role) == Role.WOLF:
                        dead.add(target)
                        death_causes[target] = "killer"
                        night_event_lines.append(
                            f"Bugun 🔪 Qotil {self._tg_mention(target_player.telegram_id, target_player.display_name)}ni o'ldirishga urundi..."
                        )
                    elif target not in healed and target not in guarded and target_player.alive:
                        if target_player.telegram_id in defended:
                            night_event_lines.append("🤹🏻 Aferist tun sirlarini chalkashtirib yubordi...")
                        else:
                            dead.add(target)
                            death_causes[target] = "killer"
                            night_event_lines.append(
                                f"Bugun 🔪 Qotil {self._tg_mention(target_player.telegram_id, target_player.display_name)}ni o'ldirishga urundi..."
                            )
                    elif target in guarded:
                        night_event_lines.append("🛡 Qo'riqchi Qotilning yo'lini to'sdi...")
                    else:
                        night_event_lines.append("👨🏼‍⚕️️Doktor qotilning rejasini buzdi...")

            for target in commissar_shots:
                target_player = player_map.get(target)
                if target_player is None:
                    continue
                if Role(target_player.role) == Role.WOLF:
                    target_player.role = Role.SERGEANT.value
                    target_player.team = Team.CITY.value
                    transformed.append(f"🐺 {target_player.display_name} serjantga aylandi")
                elif target not in healed and target not in guarded:
                    dead.add(target)
                    death_causes[target] = "commissar"
                    night_event_lines.append(
                        f"🕵🏼 Komissar katani {self._tg_mention(target_player.telegram_id, target_player.display_name)}ni otdi..."
                    )
                elif target in guarded:
                    night_event_lines.append("🛡 Qo'riqchi Komissarning o'qidan keyin ham bir jonni saqladi...")
                else:
                    night_event_lines.append("👨🏼‍⚕️️Doktor Komissar o'qidan keyin ham bir jonni saqladi...")

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
                    if victim_id in mafia_kills:
                        mafia_actors = [
                            a.actor_telegram_id
                            for a in actions
                            if a.action_type == ActionType.KILL.value and a.details == "kill"
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
                    attacker_player = player_map.get(attacker)
                    if attacker_player:
                        night_event_lines.append(
                            f"kechiradimi yoki o'ldiradimi 🧙‍ Sehrgar {self._tg_mention(attacker_player.telegram_id, attacker_player.display_name)}ni o'ldirdi."
                        )

            for dead_id in dead:
                pl = player_map.get(dead_id)
                if pl:
                    pl.alive = False
                    pl.death_day = game.day_number + 1
                    night_event_lines.append(self._role_fall_line(pl))
                    if dead_id in mafia_dead:
                        if pl.last_words:
                            night_event_lines.append(self._last_words_line(pl, pl.last_words))
                        else:
                            pl.awaiting_last_words = True
                            last_words_prompts.append((pl.telegram_id, pl.display_name))

            night_event_lines.extend(self._apply_role_successions(alive_players, dead))

            game.phase = GamePhase.DAY_DISCUSSION.value
            game.day_number += 1
            await session.commit()

            chat_id = game.chat_id
            lang = await self.get_group_language(chat_id)
            alive_after_night = [player for player in alive_players if player.alive]
            dead_players = [player_map[player_id] for player_id in dead if player_id in player_map]
            summary = self._build_day_result_text(
                day_number=game.day_number,
                alive_players=alive_after_night,
                dead_players=dead_players,
                transformed=transformed,
                night_activity_lines=night_activity_lines,
                night_event_lines=night_event_lines,
                death_causes=death_causes,
            )

        await self._send_phase_media(bot, chat_id, is_night=False, lang=lang)
        await bot.send_message(chat_id, summary)

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
            if target is None:
                continue
            seen_team = target.team
            if Role(target.role) == Role.SPY:
                seen_team = Team.CITY.value
            if target_id in defended and target.team == Team.MAFIA.value:
                seen_team = Team.CITY.value
            team_uz = {
                Team.CITY.value: "Tinch aholi",
                Team.MAFIA.value: "Mafiya",
                Team.KILLER.value: "Neytral",
                Team.NEUTRAL.value: "Neytral",
            }.get(seen_team, seen_team)
            try:
                await bot.send_message(commissar_id, f"🕵🏻 Tekshiruv: {target.display_name} → <b>{team_uz}</b>")
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

        await bot.send_message(chat_id, "🗣 Muhokama vaqti boshlandi.")
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

            user = (await session.execute(select(User).where(User.telegram_id == target_id))).scalar_one_or_none()
            if user and user.vote_protection > 0:
                user.vote_protection -= 1
                await session.commit()
                return False, "Bu o'yinchi ovoz berishdan himoyalangan."

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
            await session.commit()
            chat_id = game.chat_id
            voter_name = self._tg_mention(voter.telegram_id, voter.display_name)
            target_name = self._tg_mention(target.telegram_id, target_display_name)

        await bot.send_message(chat_id, f"{voter_name} -- {target_name} ga ovoz berdi")
        return True, f"Siz {target_display_name} ga ovoz berdingiz."

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
            player = (
                await session.execute(
                    select(GamePlayer)
                    .join(Game, Game.id == GamePlayer.game_id)
                    .where(
                        Game.status == GameStatus.ACTIVE.value,
                        GamePlayer.telegram_id == telegram_id,
                    )
                    .order_by(Game.id.desc())
                )
            ).scalars().first()
            if player is None:
                return False, "Siz aktiv o'yinda emassiz."
            player.last_words = cleaned[:500]
            player.awaiting_last_words = False
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
                await bot.send_message(
                    chat_id,
                    "<b>Ovoz berish natijalari:</b>\n"
                    f"0 👍  |  {len(alive)} 👎\n\n"
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
    ) -> tuple[bool, str]:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value or game.phase != GamePhase.DAY_CONFIRM.value:
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
                return False, "Nomzod topilmadi yoki allaqachon o'lgan."
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
                existing.approve = confirmed
                existing.target_telegram_id = target_id
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
            await session.commit()
            return True, "Ovozingiz qabul qilindi."

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
            judge.judge_cancel_used = True
            judge.inactive_rounds = 0
            game.phase = GamePhase.NIGHT.value
            game.night_number += 1
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
                no_votes = max(0, len(await self._alive_players(session, game_id)) - yes_votes)
                target.alive = False
                target.death_day = game.day_number
                vote_text = (
                    "<b>Ovoz berish natijalari:</b>\n"
                    f"{yes_votes} 👍  |  {no_votes} 👎\n\n"
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
                        await session.commit()
                        await bot.send_message(chat_id, f"🧞‍♂️ Afsungar bilan birga {victim.display_name} ham ketdi.")
            else:
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

            roles = [Role(p.role) for p in alive]
            teams = [p.team for p in alive]

            if len(alive) == 1 and Role(alive[0].role) == Role.KILLER:
                return Team.KILLER
            if len(alive) == 1 and Role(alive[0].role) == Role.JESTER:
                return Team.NEUTRAL

            mafia_count = sum(1 for t in teams if t == Team.MAFIA.value)
            city_count = sum(1 for t in teams if t == Team.CITY.value)
            killer_count = sum(1 for r in roles if r == Role.KILLER)
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
                    is_winner = Role(p.role) == Role.KILLER and p.alive
                elif winner_team == Team.NEUTRAL:
                    is_winner = Role(p.role) == Role.JESTER
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

            await session.commit()
            chat_id = game.chat_id
            duration_seconds = int((game.ended_at - game.started_at).total_seconds()) if game.ended_at else 0

        self._cleanup_jobs(game_id)

        winner_lines = [f"{idx}. {p.display_name} - {role_label(p.role)}" for idx, p in enumerate(winners, 1)]
        other_lines = [f"{idx}. {p.display_name} - {role_label(p.role)}" for idx, p in enumerate(losers, 1)]
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
                    reply_markup=profile_dashboard_keyboard(self.settings, is_admin=p.telegram_id in self.settings.admin_ids),
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
        return (
            f"⭐ ID: <code>{user.telegram_id}</code>\n\n"
            "👤\n\n"
            f"💵 Dollar: <b>{user.dollar}</b>\n"
            f"💎 Olmos: <b>{user.diamonds}</b>\n\n"
            f"🛡 Himoya: <b>{user.protection}</b>\n"
            f"⛑ Qotildan himoya: <b>{user.killer_protection}</b>\n"
            f"⚖️ Ovoz berishni himoya qilish: <b>{user.vote_protection}</b>\n"
            f"🔫 Miltiq: <b>{user.gun}</b>\n\n"
            f"🎭 Maska: <b>{user.mask}</b>\n"
            f"📁 Soxta hujjat: <b>{user.fake_document}</b>\n"
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

    async def active_game_for_chat(self, chat_id: int) -> Optional[Game]:
        async with self.session_factory() as session:
            return await self.find_active_game(session, chat_id)

    async def should_delete_message_for_non_player(self, chat_id: int, user_id: int) -> bool:
        async with self.session_factory() as session:
            game = await self.find_active_game(session, chat_id)
            if game is None or game.status != GameStatus.ACTIVE.value:
                return False
            participant = (
                await session.execute(
                    select(GamePlayer.id).where(
                        GamePlayer.game_id == game.id,
                        GamePlayer.telegram_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            return participant is None

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
                    )
                )
            ).scalars().all()
        for game in games:
            if game.registration_ends_at and self._ensure_utc(game.registration_ends_at) <= now:
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

    async def buy_shop_item(self, telegram_id: int, item_key: str) -> tuple[bool, str]:
        prices: dict[str, tuple[int, str, Union[int, str]]] = {
            "protection": (120, "protection", 1),
            "killer_protection": (100, "killer_protection", 1),
            "vote_protection": (80, "vote_protection", 1),
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
            stmt = select(PremiumGroup).order_by(PremiumGroup.id.desc())
            if not include_inactive:
                stmt = stmt.where(PremiumGroup.is_active.is_(True))
            return (await session.execute(stmt)).scalars().all()

    async def premium_groups_text(self, include_inactive: bool = False) -> str:
        groups = await self.premium_groups(include_inactive=include_inactive)
        if not groups:
            return "🎲 <b>Premium guruhlar</b>\n\nHozircha premium guruh qo'shilmagan."
        lines = ["🎲 <b>Premium guruhlar</b>\n"]
        for idx, group in enumerate(groups, 1):
            status = "active" if group.is_active else "inactive"
            lines.append(f"{idx}. <b>{group.title}</b> - 💎 {group.diamond_price} ({status})")
        return "\n".join(lines)

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

    async def update_group_setting(self, chat_id: int, field: str, value: int) -> None:
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                group = Group(chat_id=chat_id, title="Group")
                session.add(group)
            if field == "registration_timeout":
                group.registration_timeout = max(10, value)
            elif field == "min_players":
                group.min_players = max(4, min(value, 20))
            await session.commit()
