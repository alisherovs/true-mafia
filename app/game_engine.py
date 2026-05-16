from __future__ import annotations

from typing import Any, Optional, Union
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
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile, User as TgUser
from aiogram.utils.formatting import Bold, Code, CustomEmoji, Text, TextLink
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import BASE_DIR, Settings
from app.enums import ActionType, GamePhase, GameStatus, LogType, Role, Team
from app.keyboards import (
    confirm_hang_keyboard,
    commissar_action_keyboard,
    commissar_target_keyboard,
    go_private_keyboard,
    go_role_private_keyboard,
    go_vote_private_keyboard,
    go_group_keyboard,
    group_url_from_chat_id,
    hero_game_keyboard,
    hero_market_buy_keyboard,
    judge_cancel_keyboard,
    lobby_keyboard,
    miner_keyboard,
    profile_dashboard_keyboard,
    sorcerer_judgement_keyboard,
    sorcerer_hang_revenge_keyboard,
    target_keyboard,
    vote_keyboard,
)
from app.models import (
    BotSetting,
    DiamondTransaction,
    DollarTransaction,
    Game,
    GameLog,
    GamePlayer,
    Group,
    HangVote,
    Hero,
    NightAction,
    NightPrompt,
    PremiumGroup,
    PremiumBlockedUser,
    PremiumGroupContribution,
    SkipDecision,
    User,
    Vote,
)
from app.roles import (
    ACTIVE_ROLE_POOL,
    GAME_MODES,
    ROLE_META,
    SHOP_ROLE_BY_VALUE,
    build_role_set,
    normalize_game_mode,
    role_label,
    role_preset_label,
    role_preset_max_players,
    role_team,
)
from app.hero import (
    HERO_ADD_POINTS_AMOUNT,
    HERO_ADD_POINTS_PRICE_DIAMONDS,
    HERO_ATTACK_ROLES,
    HERO_BUY_PRICE_DIAMONDS,
    HERO_CANCEL_SALE_PRICE_DIAMONDS,
    HERO_DEFAULT_CHARGE,
    HERO_FULL_DEFENSE_PERCENT,
    HERO_DEFAULT_HP,
    HERO_DEFAULT_NAME,
    HERO_LEVELS,
    HERO_MARKET_CHANNEL_KEY,
    HERO_MAX_CHARGE,
    HERO_RECHARGE_PRICE_DOLLAR,
    HERO_RENAME_PRICE_DOLLAR,
    HERO_UPGRADE_DEFENSE_PRICE_DOLLAR,
    hero_level_for_points,
    safe_hero_name,
    sanitize_hero_name,
)
from app.group_settings import GroupSettingsManager
from app.scheduler import scheduler
from app.texts import t

WELCOME_ENABLED_KEY = "welcome_enabled"
WELCOME_TEXT_KEY = "welcome_text"
WELCOME_MEDIA_TYPE_KEY = "welcome_media_type"
WELCOME_MEDIA_FILE_ID_KEY = "welcome_media_file_id"
WELCOME_DEFAULT_TEXT = "guruhga xush kelibsiz!"
DOLLAR_EMOJI_ID = "5409048419211682843"
DIAMOND_EMOJI_ID = "5427168083074628963"
SWORD_EMOJI_ID = "5431774564348605707"
SKULL_EMOJI_ID = "5357199488115030155"

logger = logging.getLogger(__name__)

PREMIUM_RESET_INTERVAL_MINUTES_KEY = "premium_reset_interval_minutes"
DIAMOND_LOG_LAST_SENT_ID_KEY = "diamond_log_last_sent_id"
ADMIN_GROUP_ID_KEY = "admin_group_id"
DIAMOND_LOG_MIN_AMOUNT = 20

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
        self._chat_permission_cache: dict[tuple[int, str], tuple[float, str]] = {}
        self._cache_ttl_seconds = 10.0
        self._return_url_cache_ttl_seconds = 3600.0
        self._chat_permission_cache_ttl = 5.0
        self._cache_limit = 20000
        self._pending_sorcerer_judgements: dict[tuple[int, int, int], tuple[float, str]] = {}

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

    def invalidate_chat_permission_cache(self, chat_id: int) -> None:
        for phase in ("night", "day"):
            self._chat_permission_cache.pop((chat_id, phase), None)

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

    async def _safe_send_message(self, bot: Bot, chat_id: int, text: str, **kwargs: Any) -> Any | None:
        try:
            return await bot.send_message(chat_id, text, **kwargs)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Unable to send message to chat_id=%s: %s", chat_id, exc)
            return None
        except Exception:
            logger.exception("Unexpected error while sending message to chat_id=%s", chat_id)
            return None

    async def _safe_edit_message_reply_markup(
        self,
        bot: Bot,
        *,
        chat_id: int,
        message_id: int,
        reply_markup: object | None = None,
    ) -> bool:
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
            return True
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Unable to edit reply markup chat_id=%s message_id=%s: %s", chat_id, message_id, exc)
            return False
        except Exception:
            logger.exception("Unexpected error while editing reply markup chat_id=%s message_id=%s", chat_id, message_id)
            return False

    def _news_bonus_channel_id(self) -> str:
        raw = (self.settings.news_bonus_channel or "@WorldMafiaNews").strip()
        if not raw:
            return "@WorldMafiaNews"
        if raw.startswith("@"):
            return raw
        normalized = self.normalize_telegram_url(raw)
        if normalized.startswith("https://t.me/"):
            path = normalized.removeprefix("https://t.me/").strip("/")
            if path and "/" not in path and not path.startswith("+"):
                return f"@{path}"
        return raw

    async def user_has_news_bonus(self, bot: Bot, user_id: int) -> bool:
        try:
            member = await bot.get_chat_member(self._news_bonus_channel_id(), user_id)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Unable to check news bonus subscription user_id=%s: %s", user_id, exc)
            return False
        except Exception:
            logger.exception("Unexpected error while checking news bonus subscription user_id=%s", user_id)
            return False
        return member.status not in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}

    async def news_bonus_subscriber_ids(self, bot: Bot, user_ids: list[int]) -> set[int]:
        checks = await asyncio.gather(
            *(self.user_has_news_bonus(bot, user_id) for user_id in user_ids),
            return_exceptions=True,
        )
        return {
            user_id
            for user_id, subscribed in zip(user_ids, checks)
            if subscribed is True
        }

    @staticmethod
    def _record_diamond_transaction(
        session: AsyncSession,
        user: User,
        amount: int,
        action: str,
        *,
        note: str = "",
        counterparty: Optional[User] = None,
        chat_id: Optional[int] = None,
    ) -> None:
        if amount == 0:
            return
        session.add(
            DiamondTransaction(
                user_telegram_id=user.telegram_id,
                user_name=(user.display_name or "User")[:255],
                amount=int(amount),
                balance_after=int(user.diamonds or 0),
                action=action[:64],
                note=(note or None),
                counterparty_telegram_id=counterparty.telegram_id if counterparty else None,
                counterparty_name=(counterparty.display_name or "User")[:255] if counterparty else None,
                chat_id=chat_id,
            )
        )

    async def _get_bot_setting_value(self, session: AsyncSession, key: str, default: str = "") -> str:
        setting = (await session.execute(select(BotSetting).where(BotSetting.key == key))).scalar_one_or_none()
        return str(setting.value) if setting and setting.value is not None else default

    async def _set_bot_setting_value(self, session: AsyncSession, key: str, value: str) -> None:
        setting = (await session.execute(select(BotSetting).where(BotSetting.key == key))).scalar_one_or_none()
        if setting is None:
            session.add(BotSetting(key=key, value=value))
        else:
            setting.value = value

    @staticmethod
    def _format_minutes(minutes: int) -> str:
        minutes = max(0, int(minutes))
        if minutes == 0:
            return "o'chirilgan"
        hours, remainder = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        parts: list[str] = []
        if days:
            parts.append(f"{days} kun")
        if hours:
            parts.append(f"{hours} soat")
        if remainder:
            parts.append(f"{remainder} daqiqa")
        return " ".join(parts) or f"{minutes} daqiqa"

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
        if cause == "mashka":
            return f"{base} Mashka hujumi oqibatida halok bo'ldi..."
        if cause == "miner":
            return f"{base} o'lim koniga qulab tushdi..."
        if cause == "arsonist":
            return f"{base} G'azabkor alangasida kuyib ketdi..."
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
        elif cause == "mashka":
            visitor = "🧤 Mashka"
        elif cause == "miner":
            visitor = "👷 o'lim koni"
        elif cause == "arsonist":
            visitor = "🧟 G'azabkor alangasi"
        else:
            visitor = "noma'lum mehmon"
        return f"Tunda {role} {name}...\nvaxshiylarcha o'ldirildi. Aytishlaricha unikiga {visitor} kelgan."

    @staticmethod
    def _death_visitor_label(cause: Optional[str] = None, visitor_label: Optional[str] = None) -> str:
        if visitor_label:
            return visitor_label
        if cause == "mafia":
            return "🤵🏻 Don yoki Mafiya"
        if cause == "killer":
            return "🔪 Qotil"
        if cause == "commissar":
            return "🕵🏼 Komissar Katani"
        if cause == "sorcerer":
            return "🧙‍ Sehrgar qasosi"
        if cause == "mashka":
            return "🧤 Mashka"
        if cause == "miner":
            return "👷 o'lim koni"
        if cause == "arsonist":
            return "🧟 G'azabkor alangasi"
        return "noma'lum mehmon"

    def _build_alive_status_text(self, alive_players: list[GamePlayer], game: Optional[Game] = None) -> str:
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
        result = (
            "<b>Tirik o'yinchilar:</b>\n"
            f"{self._format_alive_players(alive_players)}\n\n"
            f"{groups_text}"
        )
        result += f"\n\n<b>Jami:</b> {len(alive_players)}"
        return result

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
            # Afsungar tunda o'ldirilgan va qasos olgan holatda:
            # avval afsungarning o'limi, keyin qasos qurboni ko'rsatiladi.
            ordered_dead_players = sorted(
                dead_players,
                key=lambda p: (
                    0 if Role(p.role) == Role.SORCERER and death_causes.get(p.telegram_id) != "sorcerer"
                    else 1 if death_causes.get(p.telegram_id) == "sorcerer"
                    else 2
                ),
            )
            death_lines = [
                self._death_story_line(
                    player,
                    death_causes.get(player.telegram_id),
                    death_visitors.get(player.telegram_id),
                )
                for player in ordered_dead_players
            ]
            add_once("\n\n".join(death_lines))
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
    def _is_day_blocked(player: GamePlayer, game: Game) -> bool:
        return bool(player.blocked_until_day and player.blocked_until_day >= game.day_number)

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
                return "🕵🏼 Komissar katani katani pistoletini o'qladi..."
            return "🕵🏼 Komissar katani katani yovuzlarni qidirishga ketdi..."
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
        if role == Role.PRANKSTER:
            return "🧑🏻‍🦲 Hazilkash prank qilishga ketdi"
        if role == Role.HOJIAKA:
            return "🕌 Hojiaka ehson ulashish uchun yo'lga tushdi..."
        if role == Role.MASHKA:
            return "🧤 Mashka kimnidir hamyonini nishonga oldi..."
        if role == Role.ARSONIST:
            return "🧟 G'azabkor o'zining navbatdagi nishonini belgiladi..."
        if role == Role.SNITCH:
            return None
        return None

    @staticmethod
    def _prank_message_for_role(role: Role) -> str:
        if role == Role.COMMISSAR:
            return "😂 Siz bugun kimnidur tekshirmoqchi edingiz biroq lupangizni hazilkash banan bilan almashtirib ketibdi"
        if role in {Role.DON, Role.MAFIA, Role.SPY, Role.HIRED_KILLER, Role.KILLER}:
            return "😂 Siz bugun pichogingiz bilan kimnidur oldirmoqchi edingiz biroq hazilkash sizga qoshiq ushlatib ketibdi"
        if role == Role.BUM:
            return "😂 Siz bugun ichkilik ichgani bormoqchi edingiz biroq hazilkash pivangizni mors bilan almashtirib qoyibdi"
        if role == Role.MISTRESS:
            return "😂 Siz bugun kimdur bilan kongil yozmoqchi edingiz biroq hazilkash taksistga sizni boshqa uyga jonatishini sorabdi"
        if role == Role.DOCTOR:
            return "😂 Siz bugun kimnidir davolamoqchi edingiz biroq hazilkash dorilarni shakar bilan almashtirib ketibdi"
        if role == Role.LAWYER:
            return "😂 Siz bugun kimnidir himoya qilmoqchi edingiz biroq hazilkash papkangizga bo'sh qog'oz solib ketibdi"
        if role == Role.JOURNALIST:
            return "😂 Siz bugun intervyu olmoqchi edingiz biroq hazilkash mikrofoningizni sabzi bilan almashtirib ketibdi"
        if role == Role.MINER:
            return "😂 Siz bugun konga bormoqchi edingiz biroq hazilkash belkuragingizni o'yinchoq qilib qo'yibdi"
        return "😂 Hazilkash bugungi rejangizni kulgili prankka aylantirib yubordi"

    def _last_words_line(self, player: GamePlayer, words: str) -> str:
        safe_words = escape(words.strip()[:500])
        name = self._tg_mention(player.telegram_id, player.display_name)
        return f"O'limidan oldin {name} qichqirganini eshitdi:\n{safe_words}"

    def _apply_role_successions(self, players: list[GamePlayer], dead_ids: set[int]) -> list[tuple[str, int, Role]]:
        successions: list[tuple[str, int, Role]] = []
        dead_players = [player for player in players if player.telegram_id in dead_ids]

        if any(Role(player.role) == Role.DON for player in dead_players):
            heir = next(
                (
                    player
                    for player in players
                    if player.telegram_id not in dead_ids
                    and player.alive
                    and Role(player.role) == Role.MAFIA
                ),
                None,
            )
            if heir:
                heir.role = Role.DON.value
                heir.team = Team.MAFIA.value
                successions.append((
                    "🤵🏻 Don mafiyaga meros qoldirdi.",
                    heir.telegram_id,
                    Role.DON,
                ))

        if any(Role(player.role) == Role.COMMISSAR for player in dead_players):
            heir = next(
                (
                    player
                    for player in players
                    if player.telegram_id not in dead_ids and Role(player.role) == Role.SERGEANT
                    and player.alive
                ),
                None,
            )
            if heir:
                heir.role = Role.COMMISSAR.value
                heir.team = Team.CITY.value
                successions.append((
                    "👮🏻‍♂ Serjant Komissar Katani vazifasini davom ettiradi.",
                    heir.telegram_id,
                    Role.COMMISSAR,
                ))

        return successions

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
                    night_timeout=self.settings.night_timeout,
                    day_discussion_timeout=self.settings.day_discussion_timeout,
                    day_voting_timeout=self.settings.day_voting_timeout,
                    min_players=self.settings.min_players,
                )
                session.add(group)
            else:
                group.title = title
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
                if group is None:
                    raise
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
                if active.status == GameStatus.REGISTRATION.value:
                    current_end = self._ensure_utc(active.registration_ends_at) if active.registration_ends_at else self._now_utc()
                    if current_end < self._now_utc():
                        current_end = self._now_utc()
                    active.registration_ends_at = current_end + timedelta(seconds=30)
                    active.creator_telegram_id = creator_id
                    self._add_game_log(
                        session,
                        active,
                        "registration_extended_by_game_command",
                        seconds=30,
                        registration_ends_at=active.registration_ends_at.isoformat(),
                    )
                    old_msg_id = active.lobby_message_id
                    if old_msg_id:
                        try:
                            await bot.delete_message(chat_id, old_msg_id)
                        except (TelegramBadRequest, TelegramForbiddenError):
                            pass
                    text = await self._build_lobby_text(session, active.id, lang, ended=False)
                    msg = await bot.send_message(
                        chat_id,
                        text,
                        reply_markup=lobby_keyboard(
                            lang=lang,
                            game_id=active.id,
                            bot_username=self.settings.bot_username,
                            chat_id=chat_id,
                            active=True,
                        ),
                    )
                    active.lobby_message_id = msg.message_id
                    game_id = active.id
                    await session.commit()

                    try:
                        await bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
                    except TelegramBadRequest:
                        pass
                    try:
                        await self.schedule_registration_jobs(bot, game_id)
                    except Exception:
                        logger.exception("Failed to reschedule registration jobs for game_id=%s", game_id)
                    return True, t(lang, "extended")
                return False, t(lang, "active_game_exists")

            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                group = Group(
                    chat_id=chat_id,
                    title=chat_title,
                    language=self.settings.default_language,
                    registration_timeout=self.settings.registration_timeout,
                    night_timeout=self.settings.night_timeout,
                    day_discussion_timeout=self.settings.day_discussion_timeout,
                    day_voting_timeout=self.settings.day_voting_timeout,
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
                role_preset=group.role_preset or "black23",
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
        locked_until = self._ensure_utc(user.play_locked_until) if user.play_locked_until else None
        now = self._now_utc()
        if locked_until and locked_until > now:
            remaining = int((locked_until - now).total_seconds())
            minutes = max(1, (remaining + 59) // 60)
            return False, f"⏳ Siz o'yindan chiqib ketgansiz. {minutes} daqiqadan keyin qayta qo'shilishingiz mumkin."
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
        check_winner_after = False
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None:
                return False, t(self.settings.default_language, "no_active_game")
            lang = await self.get_group_language(game.chat_id)
            chat_id = game.chat_id

            gsm = GroupSettingsManager(self.session_factory)
            gs = await gsm.get_settings(chat_id)
            if not gs.leave_allowed:
                return False, "❌ Bu guruhda /leave buyrug'i o'chirilgan."

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

            if game.status == GameStatus.REGISTRATION.value:
                self._add_game_log(session, game, "player_left", actor=player)
                await session.delete(player)
                user = (
                    await session.execute(select(User).where(User.telegram_id == tg_user_id))
                ).scalar_one_or_none()
                if user is not None:
                    user.play_locked_until = self._now_utc() + timedelta(minutes=30)
                await session.commit()
                self._invalidate_game_cache(chat_id)
                await self.update_lobby(bot, game_id)
                return True, "🚪 Siz o'yindan chiqdingiz. 30 daqiqa davomida boshqa o'yinga qo'shila olmaysiz."

            if game.status != GameStatus.ACTIVE.value:
                return False, t(lang, "cannot_leave_running")

            if not player.alive:
                return False, "Siz allaqachon o'yindan chetlatilgansiz."

            player.alive = False
            player.left_game = True
            player.death_day = game.day_number
            self._add_game_log(session, game, "player_left_active", actor=player)

            all_players = (
                await session.execute(
                    select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.id.asc())
                )
            ).scalars().all()
            succession_events = self._apply_role_successions(all_players, {tg_user_id})
            succession_notices = list(succession_events)

            user = (
                await session.execute(select(User).where(User.telegram_id == tg_user_id))
            ).scalar_one_or_none()
            if user is not None:
                user.play_locked_until = self._now_utc() + timedelta(minutes=30)
            await session.commit()
            check_winner_after = True

        self._invalidate_game_cache(chat_id)
        try:
            await bot.send_message(
                chat_id,
                f"🚪 O'yinchi o'yindan chiqib ketdi va o'yindan chetlatildi.",
            )
        except Exception:
            pass

        for line, heir_id, new_role in succession_notices:
            await bot.send_message(chat_id, line)
            try:
                await bot.send_message(
                    heir_id,
                    self._private_role_text(new_role),
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        if check_winner_after:
            try:
                winner = await self.check_winner(game_id)
                if winner:
                    await self.finish_game(bot, game_id, winner)
            except Exception:
                logger.exception("check_winner after leave failed")

        return True, "🚪 Siz o'yindan chiqdingiz. 30 daqiqa davomida boshqa o'yinga qo'shila olmaysiz."

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
            lobby_message_id = game.lobby_message_id

            group = (await session.execute(select(Group).where(Group.chat_id == game.chat_id))).scalar_one_or_none()
            min_players = max(4, group.min_players if group else self.settings.min_players)
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
        if lobby_message_id is not None:
            try:
                await bot.unpin_chat_message(chat_id=chat_id, message_id=lobby_message_id)
            except (TelegramBadRequest, TelegramForbiddenError):
                pass
        await self._safe_send_message(bot, chat_id, t(lang, "registration_ended"))
        await self.assign_roles_and_notify(bot, game_id)
        await self._safe_send_message(
            bot,
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
            role_preset = game.role_preset or (group.role_preset if group else "black23")
            users = {
                user.telegram_id: user
                for user in (
                    await session.execute(select(User).where(User.telegram_id.in_([p.telegram_id for p in players])))
                ).scalars().all()
            }
            disabled_roles: set[Role] = set()
            for user in users.values():
                if not user.next_game_disabled_role:
                    continue
                try:
                    disabled_roles.add(Role(user.next_game_disabled_role))
                except ValueError:
                    user.next_game_disabled_role = None
            gsm = GroupSettingsManager(self.session_factory)
            group_disabled_role_keys = await gsm.get_disabled_roles(game.chat_id)
            for rk in group_disabled_role_keys:
                try:
                    disabled_roles.add(Role(rk))
                except ValueError:
                    pass
            roles = build_role_set(len(players), role_preset, disabled_roles=disabled_roles)
            logger.info(
                "role_generation mode=%s player_count=%s selected_roles=%s disabled_roles=%s",
                normalize_game_mode(role_preset),
                len(players),
                [role.value for role in roles],
                [role.value for role in sorted(disabled_roles, key=lambda r: r.value)],
            )
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
                if desired in disabled_roles:
                    user.next_game_role = None
                    continue
                holder = next((p for p in players if assigned[p.telegram_id] == desired), None)
                if holder is not None and holder.telegram_id != player.telegram_id:
                    assigned[holder.telegram_id] = assigned[player.telegram_id]
                assigned[player.telegram_id] = desired
                user.next_game_role = None

            for player, role in zip(players, roles):
                user = users.get(player.telegram_id)
                if user is not None:
                    user.next_game_disabled_role = None
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
            sent = await self._safe_send_message(
                bot,
                player.telegram_id,
                self._private_role_text(role),
                reply_markup=await self.group_return_keyboard(bot, game.chat_id),
            )
            if sent is None:
                await self._safe_send_message(bot, game.chat_id, f"{player.display_name}: {t(lang, 'need_start_for_role')}")

        # Send team messages for Mafia
        mafia_team = [player for player in players if player.team == Team.MAFIA.value]
        if mafia_team:
            mafia_lines = [
                f"{idx}. {role_label(player.role)} - {self._tg_mention(player.telegram_id, player.display_name)}"
                for idx, player in enumerate(mafia_team, 1)
            ]
            mafia_text = "<b>Mafia jamoasi:</b>\n" + "\n".join(mafia_lines)
            for player in mafia_team:
                await self._safe_send_message(
                    bot,
                    player.telegram_id,
                    mafia_text,
                    reply_markup=await self.group_return_keyboard(bot, game.chat_id),
                )

        # Send team messages for Doctors
        doctors = [player for player in players if Role(player.role) == Role.DOCTOR]
        if doctors and len(doctors) > 1:
            doctor_lines = [
                f"{idx}. {self._tg_mention(player.telegram_id, player.display_name)}"
                for idx, player in enumerate(doctors, 1)
            ]
            doctor_text = "<b>👨🏼‍⚕️ Doktor jamoasi:</b>\n" + "\n".join(doctor_lines)
            for player in doctors:
                await self._safe_send_message(
                    bot,
                    player.telegram_id,
                    doctor_text,
                    reply_markup=await self.group_return_keyboard(bot, game.chat_id),
                )

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
                await self._safe_send_message(
                    bot,
                    player.telegram_id,
                    commissar_text,
                    reply_markup=await self.group_return_keyboard(bot, game.chat_id),
                )

    def _night_prompt_for_player(
        self,
        game_id: int,
        night_number: int,
        player: GamePlayer,
        alive_players: list[GamePlayer],
        miner_visits: Optional[dict[int, set[int]]] = None,
        arson_marks: Optional[dict[int, set[int]]] = None,
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
        if role == Role.PRANKSTER:
            return "😂 Kimga prank qilasiz?", target_keyboard("prank", game_id, player.telegram_id, targets)
        if role == Role.SNITCH:
            return "🤓 Kimni tekshirasiz?", target_keyboard("check", game_id, player.telegram_id, targets)
        if role == Role.HOJIAKA:
            return "🕌 Kimga ehson qilamiz?", target_keyboard("grant", game_id, player.telegram_id, targets)
        if role == Role.MASHKA:
            return "🧤 Kimdan o'g'irlaymiz?", target_keyboard("steal", game_id, player.telegram_id, targets)
        if role == Role.ARSONIST:
            marked_ids = arson_marks.get(player.telegram_id, set()) if arson_marks else set()
            unmarked_targets = [(tid, name) for tid, name in targets if tid not in marked_ids]
            if len(marked_ids) >= 3:
                return (
                    "🧟 Siz 3 nishonni belgilab bo'ldingiz.\n"
                    "Endi o'zingizni tanlasangiz, belgilanganlar bilan birga portlaysiz.",
                    target_keyboard(
                        "arson",
                        game_id,
                        player.telegram_id,
                        [(player.telegram_id, "🔥 O'zimni tanlayman")] + unmarked_targets,
                    ),
                )
            if unmarked_targets:
                return (
                    f"🧟 {len(marked_ids)}/3 nishon belgilangan.\nBugun kimni belgilaysiz?",
                    target_keyboard("arson", game_id, player.telegram_id, unmarked_targets),
                )
            return None
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
                    arson_rows = (
                        await session.execute(
                            select(NightAction.target_telegram_id).where(
                                NightAction.game_id == game_id,
                                NightAction.actor_telegram_id == telegram_id,
                                NightAction.details == "arson",
                                NightAction.target_telegram_id.is_not(None),
                            )
                        )
                    ).scalars().all()
                    arson_marks = {telegram_id: {tid for tid in arson_rows if tid and tid != telegram_id}}
                    prompt = self._night_prompt_for_player(
                        game_id,
                        game.night_number,
                        player,
                        alive,
                        arson_marks=arson_marks,
                    )
            is_night = game.phase == GamePhase.NIGHT.value
            is_alive = player.alive
            night_number = game.night_number

        if prompt:
            text, keyboard = prompt
            prompt_message = await self._safe_send_message(bot, telegram_id, text, reply_markup=keyboard)
            if prompt_message is not None:
                await self._remember_night_prompt(
                    game_id=game_id,
                    night_number=night_number,
                    user_telegram_id=telegram_id,
                    message_id=prompt_message.message_id,
                )
        elif is_night and is_alive:
            await self._safe_send_message(
                bot,
                telegram_id,
                "🌚 Bu tun uchun faol tanlov mavjud emas yoki tanlovingiz allaqachon qabul qilingan.",
            )
        else:
            await self._safe_send_message(
                bot,
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

        night_timeout = await self.group_timeout(chat_id, "night_timeout")
        run_at = datetime.now(timezone.utc) + timedelta(seconds=night_timeout)
        scheduler.add_job(
            self.resolve_night,
            "date",
            run_date=run_at,
            args=[bot, game_id],
            id=f"night_end_{game_id}",
            replace_existing=True,
        )
        try:
            await self._send_phase_media(
                bot,
                chat_id,
                is_night=True,
                lang=lang,
                game_id=game_id,
            )
        except Exception:
            logger.exception("Failed to send night phase media game_id=%s", game_id)
        await self._safe_send_message(bot, chat_id, alive_status)
        await self.send_night_prompts(bot, game_id)

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
            await self._safe_edit_message_reply_markup(
                bot,
                chat_id=prompt.user_telegram_id,
                message_id=prompt.message_id,
                reply_markup=None,
            )

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
            arson_rows = (
                await session.execute(
                    select(NightAction.actor_telegram_id, NightAction.target_telegram_id).where(
                        NightAction.game_id == game_id,
                        NightAction.details == "arson",
                        NightAction.target_telegram_id.is_not(None),
                    )
                )
            ).all()
            arson_marks: dict[int, set[int]] = defaultdict(set)
            for actor_id, target_id in arson_rows:
                if target_id is not None and actor_id != target_id:
                    arson_marks[actor_id].add(target_id)

        for player in alive:
            prompt = self._night_prompt_for_player(game_id, night, player, alive, miner_visits, arson_marks)
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
                await self._safe_send_message(bot, chat_id, f"{player.display_name}: /start orqali botga kiring.")
            except Exception:
                logger.exception("Failed to send night prompt game_id=%s user_id=%s", game_id, player.telegram_id)

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
            "prank": ActionType.PRANK,
            "grant": ActionType.GRANT,
            "steal": ActionType.STEAL,
            "arson": ActionType.CHECK,
        }
        action_type = action_map.get(action_key)
        if action_type is None:
            return False, "Unknown action"

        success_text = t(self.settings.default_language, "action_saved")
        chat_id: Optional[int] = None
        mafia_notice_ids: list[int] = []
        mafia_notice_text: Optional[str] = None
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
            night_blocked = (
                await session.execute(
                    select(NightAction.action_type).where(
                        NightAction.game_id == game_id,
                        NightAction.night_number == game.night_number,
                        NightAction.action_type.in_([ActionType.BLOCK.value, ActionType.PRANK.value]),
                        NightAction.target_telegram_id == actor_id,
                    )
                )
            ).scalar_one_or_none()
            if night_blocked is not None:
                if night_blocked == ActionType.PRANK.value:
                    return False, self._prank_message_for_role(actor_role)
                return False, "Kezuvchi sabab bu tunda hech qanday amal bajara olmaysiz."

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
                Role.PRANKSTER: {"prank"},
                Role.SNITCH: {"check"},
                Role.HOJIAKA: {"grant"},
                Role.MASHKA: {"steal"},
                Role.ARSONIST: {"arson"},
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

            if action_type in {ActionType.BLOCK, ActionType.VISIT}:
                already_targeted = (
                    await session.execute(
                        select(NightAction.id).where(
                            NightAction.game_id == game_id,
                            NightAction.actor_telegram_id == actor_id,
                            NightAction.target_telegram_id == target_id,
                            NightAction.action_type == action_type.value,
                        )
                    )
                ).scalar_one_or_none()
                if already_targeted is not None:
                    if action_type == ActionType.BLOCK:
                        return False, "Bu o'yinchini avval tanlagansiz. Boshqasini tanlang."
                    return False, "Bu o'yinchiga avval tashrif buyurgansiz. Boshqasini tanlang."
            if actor_role == Role.ARSONIST:
                if target_id != actor_id:
                    already_marked = (
                        await session.execute(
                            select(NightAction.id).where(
                                NightAction.game_id == game_id,
                                NightAction.actor_telegram_id == actor_id,
                                NightAction.details == "arson",
                                NightAction.target_telegram_id == target_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if already_marked is not None:
                        return False, "Bu o'yinchini oldin belgilagansiz. Boshqasini tanlang."
                marked_count = (
                    await session.execute(
                        select(func.count(NightAction.id)).where(
                            NightAction.game_id == game_id,
                            NightAction.actor_telegram_id == actor_id,
                            NightAction.details == "arson",
                            NightAction.target_telegram_id.is_not(None),
                            NightAction.target_telegram_id != actor_id,
                        )
                    )
                ).scalar_one()
                if target_id == actor_id and int(marked_count or 0) < 3:
                    return False, "Avval 3 xil o'yinchini belgilang, keyin o'zingizni tanlashingiz mumkin."

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
            elif action_type == ActionType.GRANT:
                success_text = f"Siz {target.display_name}ga ehson qilishni tanladingiz."
            elif action_type == ActionType.STEAL:
                success_text = f"Siz {target.display_name}dan o'g'irlashni tanladingiz."
            elif action_key == "arson":
                if target_id == actor_id:
                    success_text = "Siz o'zingizni tanladingiz. G'azabkor alangasi yoqiladi."
                else:
                    success_text = f"Siz {target.display_name}ni belgiladingiz."
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
                mafia_team = (
                    await session.execute(
                        select(GamePlayer).where(
                            GamePlayer.game_id == game_id,
                            GamePlayer.alive.is_(True),
                            GamePlayer.team == Team.MAFIA.value,
                            GamePlayer.telegram_id != actor_id,
                        )
                    )
                ).scalars().all()
                mafia_notice_ids = [member.telegram_id for member in mafia_team]
                mafia_notice_text = f"{actor.display_name} -- {target.display_name} ga ovoz berdi"
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
            if mafia_notice_text:
                for member_id in mafia_notice_ids:
                    try:
                        await bot.send_message(
                            member_id,
                            mafia_notice_text,
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
        return True, title, commissar_target_keyboard(action_key, game_id, actor_id, choices)

    async def commissar_action_menu_keyboard(
        self,
        game_id: int,
        actor_id: int,
    ) -> tuple[bool, str, Optional[object]]:
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
            can_shoot = game.night_number >= 2
        return True, "🕵🏼 Komissar katani", commissar_action_keyboard(game_id, actor_id, can_shoot=can_shoot)

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
                night_blocked = (
                    await session.execute(
                        select(NightAction.action_type).where(
                            NightAction.game_id == game_id,
                            NightAction.night_number == game.night_number,
                            NightAction.action_type.in_([ActionType.BLOCK.value, ActionType.PRANK.value]),
                            NightAction.target_telegram_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if night_blocked is not None:
                    if night_blocked == ActionType.PRANK.value:
                        return False, self._prank_message_for_role(Role(player.role))
                    return False, "Kezuvchi sabab bu tunda hech qanday amal bajara olmaysiz."
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
                if self._is_day_blocked(player, game):
                    return False, "Kezuvchi sabab bugun ovoz bera olmaysiz."
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
                if self._is_day_blocked(player, game):
                    return False, "Kezuvchi sabab bugun osish bo'yicha ovoz bera olmaysiz."
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
                if self._is_day_blocked(player, game):
                    return False, "Kezuvchi sabab bugun hech qanday amal bajara olmaysiz."

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

        if scope == "night":
            if Role(player.role) == Role.COMMISSAR:
                group_text = "🕵🏼 Komissar katani hech kimni tekshirmadi yoki otmaslikkaga qaror qildi."
                user_message = "Siz hech kimni tekshirmadi yoki otmaslikkaga qaror qildingiz."
            else:
                group_text = f"🚷 {role_label(player.role)} hech narsa qilmaslikka qaror qildi"
                user_message = "Siz hech narsa qilmaslikka qaror qildingiz."
        else:
            group_text = f"🚷 {player_name} hech kimni tanlamaslikka qaror qildi"
            user_message = "Siz hech narsa qilmaslikka qaror qildingiz."

        await bot.send_message(chat_id, group_text)
        try:
            await bot.send_message(
                user_id,
                user_message,
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
            users_by_tg = {
                user.telegram_id: user
                for user in (
                    await session.execute(select(User).where(User.telegram_id.in_(alive_ids)))
                ).scalars().all()
            }

            actions = (
                await session.execute(
                    select(NightAction).where(
                        NightAction.game_id == game_id,
                        NightAction.night_number == night,
                    ).order_by(NightAction.id.asc())
                )
            ).scalars().all()
            blocked: set[int] = set()
            defended: set[int] = set()
            guarded: set[int] = set()
            healed: set[int] = set()
            doctor_heal_targets: dict[int, int] = {}
            mistress_visit_targets: set[int] = set()
            visited: dict[int, int] = {}
            watched: dict[int, int] = {}
            visitors_by_target: dict[int, list[int]] = defaultdict(list)
            prank_targets: set[int] = set()
            dead: set[int] = set()
            prank_notices: list[tuple[int, str]] = []
            protected_group_lines: list[str] = []

            for act in actions:
                if act.actor_telegram_id not in alive_ids:
                    continue
                actor = player_map[act.actor_telegram_id]
                if not actor.alive:
                    continue
                if act.action_type == ActionType.PRANK.value and act.target_telegram_id:
                    target = player_map.get(act.target_telegram_id)
                    if target:
                        prank_targets.add(act.target_telegram_id)
                        prank_notices.append((act.target_telegram_id, self._prank_message_for_role(Role(target.role))))
                        target.blocked_until_day = game.day_number + 1
                if act.action_type == ActionType.BLOCK.value and act.target_telegram_id:
                    target = player_map.get(act.target_telegram_id)
                    target_user = users_by_tg.get(act.target_telegram_id)
                    if (
                        target_user
                        and target_user.use_drug_protection is not False
                        and (target_user.drug_protection or 0) > 0
                    ):
                        target_user.drug_protection -= 1
                        protected_group_lines.append("💊 Kimdir doridan himoyasini ishlatdi.")
                        self._add_game_log(
                            session,
                            game,
                            "drug_protection_used",
                            actor=target,
                            remaining_drug_protection=target_user.drug_protection,
                        )
                        continue
                    blocked.add(act.target_telegram_id)
                    mistress_visit_targets.add(act.target_telegram_id)
                    if target:
                        target.blocked_until_day = game.day_number + 1
                    actor_role_value = Role(actor.role)
                    if actor_role_value == Role.MISTRESS:
                        pass
                    elif actor_role_value == Role.CROOK:
                        protected_group_lines.append(
                            "🤹🏻 Bu tunda Qaroqchi kimnidir chalg'itib qo'ydi."
                        )

            for act in actions:
                if act.actor_telegram_id in blocked or act.actor_telegram_id in prank_targets:
                    continue
                if act.action_type == ActionType.HEAL.value and act.target_telegram_id:
                    heal_actor = player_map.get(act.actor_telegram_id)
                    heal_target = player_map.get(act.target_telegram_id)
                    if (
                        heal_actor is not None
                        and heal_actor.alive
                        and heal_actor.role == Role.DOCTOR.value
                        and heal_target is not None
                        and heal_target.alive
                        and act.target_telegram_id not in dead
                    ):
                        healed.add(act.target_telegram_id)
                        doctor_heal_targets[act.actor_telegram_id] = act.target_telegram_id
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
            hojiaka_grants: list[tuple[int, int]] = []
            mashka_steals: list[tuple[int, int]] = []
            mine_actions: list[tuple[int, int]] = []
            miner_protectors: set[int] = set()
            arson_actions: list[tuple[int, int]] = []
            night_activity_lines: list[str] = []

            for act in actions:
                if act.actor_telegram_id in blocked or act.actor_telegram_id in prank_targets:
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
                elif act.action_type == ActionType.CHECK.value and role == Role.SNITCH:
                    pass  # handled separately below
                elif act.action_type == ActionType.GRANT.value and role == Role.HOJIAKA:
                    hojiaka_grants.append((act.actor_telegram_id, target_id))
                elif act.action_type == ActionType.STEAL.value and role == Role.MASHKA:
                    mashka_steals.append((act.actor_telegram_id, target_id))
                elif act.details == "arson" and role == Role.ARSONIST:
                    arson_actions.append((act.actor_telegram_id, target_id))
                elif act.action_type == ActionType.MINE.value and role == Role.MINER:
                    mine_actions.append((act.actor_telegram_id, target_id))
                elif act.action_type == ActionType.MINE_PROTECT.value and role == Role.MINER:
                    miner_protectors.add(act.actor_telegram_id)

            death_causes: dict[int, str] = {}
            mafia_dead: set[int] = set()
            death_visitors: dict[int, str] = {}
            transformed: list[str] = []
            night_event_lines: list[str] = []
            protected_notices: list[tuple[int, str]] = []
            last_words_prompts: list[tuple[int, str]] = []
            miner_result_notices: list[tuple[int, str]] = []
            miner_group_lines: list[str] = []
            hojiaka_notices: list[tuple[int, str]] = []
            hojiaka_target_notices: list[tuple[int, str]] = []
            hojiaka_group_lines: list[str] = []
            mashka_notices: list[tuple[int, str]] = []
            mashka_target_notices: list[tuple[int, str]] = []
            mashka_group_lines: list[str] = []
            arsonist_inferno_triggered = False
            doctor_saved_targets: set[int] = set()
            doctor_save_notices: list[tuple[int, int, int, str]] = []

            # SNITCH resolution
            snitch_actions = [
                act for act in actions
                if act.action_type == ActionType.CHECK.value
                and act.actor_telegram_id in player_map
                and Role(player_map[act.actor_telegram_id].role) == Role.SNITCH
                and act.actor_telegram_id not in blocked
                and act.actor_telegram_id not in prank_targets
            ]
            snitch_group_lines: list[str] = []
            snitch_notices: list[tuple[int, str]] = []
            for act in snitch_actions:
                target = player_map.get(act.target_telegram_id)
                if target is None:
                    continue
                target_role = Role(target.role)
                if target_role in {Role.DON, Role.MAFIA, Role.KILLER}:
                    snitch_group_lines.append("🤓 Sotqinning izlanishlari samara berdi!")
                    snitch_group_lines.append(
                        f"🤓 Sotqin odamlarga {self._tg_mention(target.telegram_id, target.display_name)}ning {role_label(target_role)} ekanini sotib berdi."
                    )
                    snitch_notices.append((
                        act.actor_telegram_id,
                        f"🤓 Siz {self._tg_mention(target.telegram_id, target.display_name)}ni tekshirdingiz. U {role_label(target_role)} ekan! Odamlarga bu haqida xabar berildi.",
                    ))
                else:
                    snitch_group_lines.append("🤓 Sotqinning izlanishlari zoya ketdi!")
                    snitch_notices.append((
                        act.actor_telegram_id,
                        f"🤓 Siz {self._tg_mention(target.telegram_id, target.display_name)}ni tekshirdingiz. U oddiy o'yinchi ekan.",
                    ))

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
                        amount = 1
                        if user:
                            user.diamonds += amount
                            self._record_diamond_transaction(
                                session,
                                user,
                                amount,
                                "miner_reward",
                                note=f"O'yin #{game.id}: konchi {mine_number:02d}-kondan olmos topdi",
                                chat_id=game.chat_id,
                            )
                        miner_result_notices.append((actor_id, f"👷🏻‍♂️ {mine_number:02d}-kondan <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {amount} olmos topdingiz."))
                        miner_group_lines.append(
                            f"👷🏻‍♂️ Konchi konda {amount} <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> olmos topdi!"
                        )
                    elif result == "dollar":
                        amount = 50
                        if user:
                            user.dollar += amount
                        miner_result_notices.append((actor_id, f"👷🏻‍♂️ {mine_number:02d}-kondan <tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji> {amount} dollar topdingiz."))
                        miner_group_lines.append(
                            f"👷🏻‍♂️ Konchi konda {amount} <tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji> topdi!"
                        )
                    elif user and user.use_miner_protection is not False and (user.miner_protection or 0) > 0:
                        user.miner_protection -= 1
                        miner_result_notices.append(
                            (actor_id, f"👷🏻‍♂️ {mine_number:02d}-o'lim koniga tushdingiz, lekin Konchi himoyasi sizni qutqardi.")
                        )
                        miner_group_lines.append("👷🏻‍♂️ Konchi o'lim konida sirpanib ketdi, lekin himoyasi uni qutqardi!")
                    else:
                        dead.add(actor_id)
                        death_causes[actor_id] = "miner"
                        death_visitors[actor_id] = role_label(Role.MINER)
                        miner_result_notices.append((actor_id, f"👷🏻‍♂️ {mine_number:02d}-o'lim koniga tushdingiz."))
                        miner_group_lines.append("👷🏻‍♂️ Konchi konda sirpanib ketib halok bo'ldi!")

            if hojiaka_grants:
                dollar_choices = [50, 70, 90, 100, 130, 150, 170, 200, 250]
                item_choices: list[tuple[str, str]] = [
                    ("protection", "🛡 Himoya"),
                    ("killer_protection", "🧿 Qotildan himoya"),
                    ("drug_protection", "💊 Doridan himoya"),
                    ("vote_protection", "⚖️ Ovozdan himoya"),
                    ("miner_protection", "📦 Sirpanishdan himoya"),
                    ("mask", "🎭 Maska"),
                ]
                for actor_id, target_id in hojiaka_grants:
                    actor_player = player_map.get(actor_id)
                    target_player = player_map.get(target_id)
                    if actor_player is None or target_player is None:
                        continue
                    actor_user = users_by_tg.get(actor_id)
                    target_user = users_by_tg.get(target_id)
                    if actor_user is None or target_user is None:
                        continue
                    rng = random.Random(f"hojiaka:{game.id}:{game.night_number}:{actor_id}:{target_id}")
                    reward_type = rng.choices(["item", "dollar", "diamond"], weights=[50, 45, 5], k=1)[0]
                    if reward_type == "diamond":
                        amount = rng.choice([1, 1, 1, 2, 2, 3])
                        target_user.diamonds = int(target_user.diamonds or 0) + amount
                        self._record_diamond_transaction(
                            session,
                            target_user,
                            amount,
                            "hojiaka_grant",
                            note=f"O'yin #{game.id}: Hojiaka ehsoni",
                            counterparty=actor_user,
                            chat_id=game.chat_id,
                        )
                        gift_label = f"<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {amount} olmos"
                    elif reward_type == "dollar":
                        amount = rng.choice(dollar_choices)
                        target_user.dollar = int(target_user.dollar or 0) + amount
                        self._record_dollar_transaction(
                            session,
                            target_user,
                            amount,
                            "hojiaka_grant",
                            note=f"O'yin #{game.id}: Hojiaka ehsoni",
                            counterparty=actor_user,
                            chat_id=game.chat_id,
                        )
                        gift_label = f"<tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji> {amount} dollar"
                    else:
                        field, title = rng.choice(item_choices)
                        current = int(getattr(target_user, field) or 0)
                        setattr(target_user, field, current + 1)
                        gift_label = title

                    hojiaka_notices.append(
                        (actor_id, f"🕌 Siz {self._tg_mention(target_player.telegram_id, target_player.display_name)}ga {gift_label} ehson qildingiz.")
                    )
                    hojiaka_target_notices.append(
                        (target_id, f"🕌 Hojiaka sizga {gift_label} ehson ulashdi!")
                    )
                    hojiaka_group_lines.append(
                        f"🕌 Hojiaka {self._tg_mention(target_player.telegram_id, target_player.display_name)}ga "
                        f"{gift_label} ehson ulashdi."
                    )

            if mashka_steals:
                steal_dollar_choices = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
                for actor_id, target_id in mashka_steals:
                    actor_player = player_map.get(actor_id)
                    target_player = player_map.get(target_id)
                    if actor_player is None or target_player is None:
                        continue
                    actor_user = users_by_tg.get(actor_id)
                    target_user = users_by_tg.get(target_id)
                    if actor_user is None or target_user is None:
                        continue
                    rng = random.Random(f"mashka:{game.id}:{game.night_number}:{actor_id}:{target_id}")
                    steal_diamond = rng.random() < 0.1 and int(target_user.diamonds or 0) >= 1
                    if steal_diamond:
                        target_user.diamonds -= 1
                        actor_user.diamonds = int(actor_user.diamonds or 0) + 1
                        self._record_diamond_transaction(
                            session,
                            target_user,
                            -1,
                            "mashka_steal_out",
                            note=f"O'yin #{game.id}: Mashka o'g'irligi",
                            counterparty=actor_user,
                            chat_id=game.chat_id,
                        )
                        self._record_diamond_transaction(
                            session,
                            actor_user,
                            1,
                            "mashka_steal_in",
                            note=f"O'yin #{game.id}: Mashka o'g'irligi",
                            counterparty=target_user,
                            chat_id=game.chat_id,
                        )
                        stolen_label = "<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> 1 olmos"
                    else:
                        possible = [v for v in steal_dollar_choices if v <= int(target_user.dollar or 0)]
                        if not possible:
                            current_hp = int(target_player.hero_hp or HERO_DEFAULT_HP)
                            hp_loss = max(1, current_hp // 2)
                            target_player.hero_hp = max(0, current_hp - hp_loss)
                            target_max_hp = int(target_player.hero_max_hp or HERO_DEFAULT_HP)
                            if target_player.hero_hp <= 0 and target_player.telegram_id not in dead:
                                dead.add(target_player.telegram_id)
                                death_causes[target_player.telegram_id] = "mashka"
                                death_visitors[target_player.telegram_id] = role_label(Role.MASHKA)
                            mashka_notices.append(
                                (
                                    actor_id,
                                    f"🧤 Balans yo'qligi sabab {self._tg_mention(target_player.telegram_id, target_player.display_name)}dan "
                                    f"♥️ {hp_loss} jon oldingiz. Qolgan jon: ♥️ {int(target_player.hero_hp or 0)}/{target_max_hp}",
                                )
                            )
                            mashka_target_notices.append(
                                (
                                    target_id,
                                    f"🧤 Mashka sizning 50% joningizni oldi: -♥️ {hp_loss}. "
                                    f"Qolgan jon: ♥️ {int(target_player.hero_hp or 0)}/{target_max_hp}",
                                )
                            )
                            mashka_group_lines.append(
                                f"🧤 Mashka {self._tg_mention(target_player.telegram_id, target_player.display_name)}ning 50% jonini oldi."
                            )
                            continue
                        amount = rng.choice(possible)
                        target_user.dollar -= amount
                        actor_user.dollar = int(actor_user.dollar or 0) + amount
                        self._record_dollar_transaction(
                            session,
                            target_user,
                            -amount,
                            "mashka_steal_out",
                            note=f"O'yin #{game.id}: Mashka o'g'irligi",
                            counterparty=actor_user,
                            chat_id=game.chat_id,
                        )
                        self._record_dollar_transaction(
                            session,
                            actor_user,
                            amount,
                            "mashka_steal_in",
                            note=f"O'yin #{game.id}: Mashka o'g'irligi",
                            counterparty=target_user,
                            chat_id=game.chat_id,
                        )
                        stolen_label = f"<tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji> {amount} dollar"

                    mashka_notices.append(
                        (actor_id, f"🧤 Siz {self._tg_mention(target_player.telegram_id, target_player.display_name)}dan {stolen_label} o'g'irladingiz.")
                    )
                    mashka_target_notices.append(
                        (target_id, f"🧤 Mashka sizdan {stolen_label} o'g'irladi.")
                    )
                    mashka_group_lines.append(
                        f"🧤 Mashka kimdandir {stolen_label} o'g'irlab ketdi."
                    )

            arson_group_lines: list[str] = []
            for actor_id, target_id in arson_actions:
                actor_player = player_map.get(actor_id)
                if actor_player is None or not actor_player.alive:
                    continue
                if target_id != actor_id:
                    arson_group_lines.append("🧟 G'azabkor bu tunda yana bir nishonni belgiladi...")
                    continue

                marked_ids = {
                    marked_id
                    for marked_id in (
                        await session.execute(
                            select(NightAction.target_telegram_id).where(
                                NightAction.game_id == game_id,
                                NightAction.actor_telegram_id == actor_id,
                                NightAction.details == "arson",
                                NightAction.target_telegram_id.is_not(None),
                                NightAction.target_telegram_id != actor_id,
                                NightAction.night_number <= night,
                            )
                        )
                    ).scalars().all()
                    if marked_id in player_map
                }
                if len(marked_ids) < 3:
                    continue

                dead.add(actor_id)
                death_causes[actor_id] = "arsonist"
                death_visitors[actor_id] = role_label(Role.ARSONIST)
                actor_player.won = True
                arsonist_inferno_triggered = True
                arson_group_lines.append(
                    f"🧟 G'azabkor {self._tg_mention(actor_player.telegram_id, actor_player.display_name)} alangani yoqdi!"
                )
                for marked_id in marked_ids:
                    marked_player = player_map.get(marked_id)
                    if marked_player is None or not marked_player.alive:
                        continue
                    dead.add(marked_id)
                    death_causes[marked_id] = "arsonist"
                    death_visitors[marked_id] = role_label(Role.ARSONIST)
                    marked_player.won = False
                    arson_group_lines.append(
                        f"🔥 {self._tg_mention(marked_player.telegram_id, marked_player.display_name)} G'azabkor alangasida yonib ketdi."
                    )

            mafia_fallback_as_don = False
            alive_don_ids = {
                p.telegram_id
                for p in alive_players
                if p.alive and Role(p.role) == Role.DON
            }
            don_blocked = bool(alive_don_ids & (blocked | prank_targets))
            if don_blocked:
                # Kezuvchi Donni to'xtatgan bo'lsa, bu tunda mafiyaning boshqa ovozlari ham ishlamaydi.
                active_mafia_roles = {Role.DON}
                active_mafia_kills = []
            elif don_kills:
                active_mafia_roles = {Role.DON}
                active_mafia_kills = don_kills
            else:
                active_mafia_roles = {Role.MAFIA, Role.SPY, Role.HIRED_KILLER}
                active_mafia_kills = mafia_fallback_kills
                mafia_fallback_as_don = bool(mafia_fallback_kills)
            mafia_target = Counter(active_mafia_kills).most_common(1)
            if mafia_fallback_as_don and mafia_target:
                don_activity = self._night_activity_line(Role.DON, "kill")
                if don_activity:
                    night_activity_lines.append(don_activity)
            if mafia_target:
                target = mafia_target[0][0]
                target_player = player_map.get(target)
                if target_player:
                    if Role(target_player.role) == Role.WOLF:
                        target_player.role = Role.MAFIA.value
                        target_player.team = Team.MAFIA.value
                        transformed.append("🐺 Bo'ri mafiyaga aylandi")
                    elif target in healed and target_player.alive and target not in dead:
                        doctor_saved_targets.add(target)
                        healer_id = next(
                            (
                                actor_id
                                for actor_id, heal_target_id in doctor_heal_targets.items()
                                if heal_target_id == target
                            ),
                            None,
                        )
                        if healer_id is not None:
                            attacker_label = role_label(Role.DON) if mafia_fallback_as_don else role_label(Role.MAFIA)
                            doctor_save_notices.append((healer_id, target, target_player.telegram_id, attacker_label))
                    elif target not in guarded:
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
                        if mafia_fallback_as_don:
                            death_visitors[target] = role_label(Role.DON)
                        elif killer_actor:
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
                        death_visitors[target] = role_label(Role.KILLER)
                    elif target in healed and target_player.alive:
                        doctor_saved_targets.add(target)
                        healer_id = next(
                            (
                                actor_id
                                for actor_id, heal_target_id in doctor_heal_targets.items()
                                if heal_target_id == target
                            ),
                            None,
                        )
                        if healer_id is not None:
                            doctor_save_notices.append((healer_id, target, target_player.telegram_id, role_label(Role.KILLER)))
                    elif target not in guarded and target_player.alive:
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
                    transformed.append("🐺 Bo'ri serjantga aylandi")
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
                        if not await self.check_weapon_enabled(game.chat_id, "killer_protection"):
                            continue
                        user.killer_protection -= 1
                        dead.discard(victim_id)
                        death_causes.pop(victim_id, None)
                        death_visitors.pop(victim_id, None)
                        protected_notices.append((victim_id, "🧿 Qotildan himoya sizni qutqarib qoldi."))
                        protected_group_lines.append("🧿 Kimdir qotildan himoyasini ishlatdi.")
                        self._add_game_log(
                            session,
                            game,
                            "killer_protection_used",
                            target=player_map.get(victim_id),
                            remaining_killer_protection=user.killer_protection,
                        )
                    elif cause in {"mafia", "commissar"} and user.use_protection is not False and (user.protection or 0) > 0:
                        if not await self.check_weapon_enabled(game.chat_id, "protection"):
                            continue
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
                                protected_group_lines.append("🛡 Kimdir o'z himoyasini ishlatdi.")
                        self._add_game_log(
                            session,
                            game,
                            "protection_used",
                            target=player_map.get(victim_id),
                            cause=cause,
                            remaining_protection=user.protection,
                        )

            # Daydi sees what happened at the house he visited.
            witness_lines: list[tuple[int, str]] = []
            killed_targets = dead.copy()
            for observer_id, visited_id in visited.items():
                observer = player_map.get(observer_id)
                if observer is None:
                    continue
                if visited_id in killed_targets:
                    victim = player_map.get(visited_id)
                    if victim:
                        visitor = self._death_visitor_label(
                            death_causes.get(visited_id),
                            death_visitors.get(visited_id),
                        )
                        witness_lines.append(
                            (
                                observer.telegram_id,
                                "🍾 Siz kimningdir jonsiz jasadi ustida "
                                f"{self._tg_mention(victim.telegram_id, victim.display_name)} - {role_label(victim.role)} "
                                f"yonida {visitor} turganini ko'rdingiz.",
                            )
                        )
                else:
                    witness_lines.append(
                        (
                            observer.telegram_id,
                            "🍾 Siz shishani oldingiz va uyingizga qaytdingiz! Shubhali narsani ko'rmadingiz!",
                        )
                    )

            watcher_lines: list[tuple[int, str]] = []
            for watcher_id, watched_id in watched.items():
                watcher = player_map.get(watcher_id)
                watched_player = player_map.get(watched_id)
                if watcher is None or watched_player is None:
                    continue
                visitor_ids = [visitor_id for visitor_id in visitors_by_target.get(watched_id, []) if visitor_id in player_map]
                if visitor_ids:
                    if Role(watcher.role) == Role.JOURNALIST:
                        visitor_names = []
                        for visitor_id in visitor_ids:
                            visitor = player_map[visitor_id]
                            visitor_text = self._tg_mention(visitor.telegram_id, visitor.display_name)
                            if Role(visitor.role) != Role.COMMISSAR:
                                visitor_text += f" - {role_label(visitor.role)}"
                            visitor_names.append(visitor_text)
                    else:
                        visitor_names = [
                            self._tg_mention(visitor_id, player_map[visitor_id].display_name)
                            for visitor_id in visitor_ids
                        ]
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

            sorcerer_revenge_candidates: list[tuple[int, int, Role | None]] = []
            for victim_id in list(dead):
                victim = player_map.get(victim_id)
                if victim and Role(victim.role) == Role.SORCERER:
                    attacker = None
                    all_mafia_kill_targets = set(don_kills) | set(mafia_fallback_kills)
                    if victim_id in all_mafia_kill_targets:
                        alive_don = next(
                            (
                                p.telegram_id
                                for p in alive_players
                                if Role(p.role) == Role.DON and p.telegram_id in alive_ids
                            ),
                            None,
                        )
                        if alive_don is not None:
                            attacker = alive_don
                        else:
                            mafia_actors = [
                                a.actor_telegram_id
                                for a in actions
                                if a.action_type == ActionType.KILL.value
                                and a.target_telegram_id == victim_id
                                and a.actor_telegram_id in player_map
                                and Role(player_map[a.actor_telegram_id].role) in {Role.DON, Role.MAFIA, Role.SPY, Role.HIRED_KILLER}
                            ]
                            attacker = mafia_actors[0] if mafia_actors else None
                    if attacker is None and victim_id in killer_kills:
                        killer_actors = [
                            a.actor_telegram_id
                            for a in actions
                            if a.action_type == ActionType.KILL.value and a.details == "killer"
                        ]
                        attacker = killer_actors[0] if killer_actors else None
                    elif victim_id in commissar_shots:
                        shooter = [a.actor_telegram_id for a in actions if a.action_type == ActionType.SHOOT.value]
                        attacker = shooter[0] if shooter else None
                    attacker_role = None
                    if attacker and attacker in player_map and player_map[attacker].role:
                        attacker_role = Role(player_map[attacker].role)
                    if attacker:
                        sorcerer_revenge_candidates.append((victim_id, attacker, attacker_role))

            for victim_id, attacker, attacker_role in sorcerer_revenge_candidates:
                sorcerer_player = player_map.get(victim_id)
                if sorcerer_player is not None:
                    sorcerer_player.sorcerer_revenge_used = True
                    sorcerer_player.won = attacker_role in {
                        Role.DON,
                        Role.MAFIA,
                        Role.SPY,
                        Role.HIRED_KILLER,
                        Role.KILLER,
                    }
                    attacker_player = player_map.get(attacker)
                    if attacker_player is not None:
                        role_text = role_label(attacker_player.role)
                        try:
                            await bot.send_message(
                                sorcerer_player.telegram_id,
                                f"🧙‍♂️ Sizni {role_text} oldirishga harakat qildi.",
                                reply_markup=await self.group_return_keyboard(bot, game.chat_id),
                            )
                        except TelegramForbiddenError:
                            pass
                if attacker in alive_ids and attacker_role == Role.HIRED_KILLER:
                    dead.add(attacker)
                    death_causes[attacker] = "sorcerer"
                    death_visitors[attacker] = role_label(Role.SORCERER.value)
                    attacker_player = player_map.get(attacker)
                    if attacker_player is not None:
                        night_event_lines.append(
                            f"💣 Sehrgar yollanma qotilni avtomatik jahannamga olib ketdi: "
                            f"{self._tg_mention(attacker_player.telegram_id, attacker_player.display_name)}."
                        )

            sorcerer_judgement_prompts: list[tuple[int, int, int, str]] = []
            for victim_id in list(dead):
                victim = player_map.get(victim_id)
                if victim is None or Role(victim.role) != Role.MAQ:
                    continue
                attacker_id: Optional[int] = None
                attacker_role: Optional[Role] = None

                if victim_id in set(don_kills) | set(mafia_fallback_kills):
                    attacker_id = next(
                        (
                            a.actor_telegram_id
                            for a in actions
                            if a.action_type == ActionType.KILL.value
                            and a.target_telegram_id == victim_id
                            and a.actor_telegram_id in player_map
                            and Role(player_map[a.actor_telegram_id].role) == Role.DON
                        ),
                        None,
                    )
                    if attacker_id is not None:
                        attacker_role = Role.DON
                if attacker_id is None and victim_id in killer_kills:
                    attacker_id = next(
                        (
                            a.actor_telegram_id
                            for a in actions
                            if a.action_type == ActionType.KILL.value
                            and a.details == "killer"
                            and a.target_telegram_id == victim_id
                        ),
                        None,
                    )
                    if attacker_id is not None:
                        attacker_role = Role.KILLER
                if attacker_id is None and victim_id in commissar_shots:
                    attacker_id = next(
                        (
                            a.actor_telegram_id
                            for a in actions
                            if a.action_type == ActionType.SHOOT.value and a.target_telegram_id == victim_id
                        ),
                        None,
                    )
                    if attacker_id is not None:
                        attacker_role = Role.COMMISSAR

                if attacker_id is None or attacker_role is None:
                    continue

                dead.discard(victim_id)
                death_causes.pop(victim_id, None)
                death_visitors.pop(victim_id, None)
                sorcerer_judgement_prompts.append((game.id, victim_id, attacker_id, role_label(attacker_role)))

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

            succession_events = self._apply_role_successions(alive_players, dead)
            night_event_lines.extend(line for line, _, _ in succession_events)

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
            alive_status = self._build_alive_status_text(alive_after_night, game)
            story_messages = self._build_night_story_messages(
                dead_players=dead_players,
                transformed=transformed,
                night_activity_lines=night_activity_lines,
                night_event_lines=night_event_lines,
                death_causes=death_causes,
                death_visitors=death_visitors,
            )
            doctor_idle_actor_ids = sorted(
                {
                    actor_id
                    for actor_id, target_id in doctor_heal_targets.items()
                    if target_id not in doctor_saved_targets or target_id in dead
                }
            )
            mistress_visit_ids = [
                player_id
                for player_id in mistress_visit_targets
                if player_id in player_map and player_id not in dead
            ]
            succession_notices = list(succession_events)
            doctor_save_notices = list(doctor_save_notices)
            sorcerer_judgement_prompts = list(dict.fromkeys(sorcerer_judgement_prompts))

        await self._clear_night_prompt_buttons(bot, game_id, night)
        try:
            await self._send_phase_media(
                bot,
                chat_id,
                is_night=False,
                lang=lang,
                game_id=game_id,
                caption_override=day_caption,
            )
        except Exception:
            logger.exception("Failed to send day phase media game_id=%s", game_id)
        await self._safe_send_message(bot, chat_id, alive_status)
        for story_message in story_messages:
            await self._safe_send_message(bot, chat_id, story_message)
            await asyncio.sleep(0.15)

        for doctor_id, saved_id, saved_telegram_id, attacker_label in doctor_save_notices:
            saved_player = player_map.get(saved_id)
            saved_name = self._tg_mention(
                saved_telegram_id,
                saved_player.display_name if saved_player else str(saved_telegram_id),
            )
            try:
                await bot.send_message(
                    doctor_id,
                    f"🩺 Siz {saved_name}ni {attacker_label}dan qutqardingiz.",
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass
            except Exception:
                logger.exception("Failed to deliver doctor save notification to doctor")

            try:
                await bot.send_message(
                    saved_telegram_id,
                    f"🩺 Shifokor sizni {attacker_label}dan qutqarib qoldi.",
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass
            except Exception:
                logger.exception("Failed to deliver doctor save notification to saved target")

        for telegram_id in doctor_idle_actor_ids:
            if telegram_id in dead:
                continue
            try:
                await bot.send_message(
                    telegram_id,
                    "🌙 Bugun tinch tun o'tdi. Sizning yordamingiz kerak bo'lmadi.",
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass
            except Exception:
                logger.exception("Failed to deliver doctor idle notification")

        for telegram_id in mistress_visit_ids:
            try:
                await bot.send_message(
                    telegram_id,
                    '"Ana 💊dori tasir qila boshladi endi sen bir kun uxlaysan...", - dedi 💃 Kezuvchi',
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        for telegram_id, text in prank_notices:
            if telegram_id in dead:
                continue
            try:
                await bot.send_message(
                    telegram_id,
                    text,
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

        for telegram_id, text in snitch_notices:
            try:
                await bot.send_message(
                    telegram_id,
                    text,
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        for telegram_id, text in hojiaka_notices:
            try:
                await bot.send_message(
                    telegram_id,
                    text,
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        for telegram_id, text in hojiaka_target_notices:
            try:
                await bot.send_message(
                    telegram_id,
                    text,
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        for telegram_id, text in mashka_notices:
            try:
                await bot.send_message(
                    telegram_id,
                    text,
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        for telegram_id, text in mashka_target_notices:
            try:
                await bot.send_message(
                    telegram_id,
                    text,
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        now_mono = self._monotonic()
        for game_id_prompt, sorcerer_id, attacker_id, attacker_role_name in sorcerer_judgement_prompts:
            self._pending_sorcerer_judgements[(game_id_prompt, sorcerer_id, attacker_id)] = (
                now_mono + 3600.0,
                attacker_role_name,
            )
            try:
                await bot.send_message(
                    sorcerer_id,
                    f"🧙‍♂️ {attacker_role_name} sizni oldirishga harakat qildi.\nQaroringizni tanlang:",
                    reply_markup=sorcerer_judgement_keyboard(
                        game_id=game_id_prompt,
                        sorcerer_id=sorcerer_id,
                        attacker_id=attacker_id,
                    ),
                )
            except TelegramForbiddenError:
                pass

        for line in miner_group_lines:
            await self._safe_send_message(bot, chat_id, line)

        for line in dict.fromkeys(protected_group_lines):
            await self._safe_send_message(bot, chat_id, line)

        for line in snitch_group_lines:
            await self._safe_send_message(bot, chat_id, line)

        for line in dict.fromkeys(hojiaka_group_lines):
            await self._safe_send_message(bot, chat_id, line)

        for line in dict.fromkeys(mashka_group_lines):
            await self._safe_send_message(bot, chat_id, line)

        for line in dict.fromkeys(arson_group_lines):
            await self._safe_send_message(bot, chat_id, line)

        for _, telegram_id, new_role in succession_notices:
            try:
                await self._safe_send_message(
                    bot,
                    telegram_id,
                    self._private_role_text(new_role),
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        for telegram_id, _ in last_words_prompts:
            try:
                await bot.send_message(
                    telegram_id,
                    "Sizni shavqatsizlarcha o'ldirishdi :(\nSo'nggi so'zingni aytishing mumkin:",
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
                sergeant_ids = (
                    await s2.execute(
                        select(GamePlayer.telegram_id).where(
                            GamePlayer.game_id == game_id,
                            GamePlayer.alive.is_(True),
                            GamePlayer.role == Role.SERGEANT.value,
                            GamePlayer.telegram_id != commissar_id,
                        )
                    )
                ).scalars().all()
            if target is None:
                continue
            seen_role = Role(target.role)
            if Role(target.role) == Role.SPY:
                seen_role = Role.CITIZEN
            if target_id in defended and target.team == Team.MAFIA.value:
                seen_role = Role.CITIZEN
            if hidden_by_item:
                seen_role = Role.CITIZEN
                try:
                    await bot.send_message(
                        target_id,
                        "🎭 Maska yoki 📁 soxta hujjat komissar tekshiruvini yashirdi.",
                        reply_markup=await self.group_return_keyboard(bot, chat_id),
                    )
                except TelegramForbiddenError:
                    pass
            try:
                check_text = self._commissar_check_result_text(target, seen_role)
                await bot.send_message(commissar_id, check_text)
            except TelegramForbiddenError:
                pass
            for sergeant_id in sergeant_ids:
                try:
                    await bot.send_message(
                        sergeant_id,
                        check_text,
                        reply_markup=await self.group_return_keyboard(bot, chat_id),
                    )
                except TelegramForbiddenError:
                    pass
            try:
                await bot.send_message(
                    target_id,
                    "🕵🏼 Kimdir rolingizga judayam qiziqdi.",
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        for observer_id, text in witness_lines:
            try:
                await bot.send_message(
                    observer_id,
                    text,
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass

        for watcher_id, text in watcher_lines:
            try:
                await bot.send_message(watcher_id, text)
            except TelegramForbiddenError:
                pass

        if arsonist_inferno_triggered:
            await self.finish_game(bot, game_id, Team.KILLER)
            return

        winner = await self.check_winner(game_id)
        if winner:
            await self.finish_game(bot, game_id, winner)
            return

        try:
            await self.send_hero_phase_prompts(bot, game_id)
        except Exception:
            logger.exception("Failed to send hero phase prompts game_id=%s", game_id)

        discussion_timeout = await self.group_timeout(chat_id, "day_discussion_timeout")
        scheduler.add_job(
            self.start_voting,
            "date",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=discussion_timeout),
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
            for player in alive:
                player.hero_defense_active = False
                player.hero_defense_amount = 0
            self._add_game_log(
                session,
                game,
                "voting_started",
                alive_count=len(alive),
                timeout=await self.group_timeout(game.chat_id, "day_voting_timeout"),
            )
            await session.commit()
            choices = [(p.telegram_id, p.display_name) for p in alive]
            lang = await self.get_group_language(game.chat_id)
            voting_timeout = await self.group_timeout(game.chat_id, "day_voting_timeout")

        if len(choices) <= 1:
            winner = await self.check_winner(game_id)
            if winner:
                await self.finish_game(bot, game_id, winner)
            return

        scheduler.add_job(
            self.resolve_voting,
            "date",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=voting_timeout),
            args=[bot, game_id],
            id=f"vote_end_{game_id}",
            replace_existing=True,
        )
        await self._safe_send_message(
            bot,
            await self._game_chat_id(game_id),
            "Aybdorlarni aniqlash va jazolash vaqti keldi.\n"
            f"Ovoz berish uchun {voting_timeout} sekund.",
            reply_markup=go_vote_private_keyboard(self.settings, game_id),
        )
        for player_id, _ in choices:
            ok, _ = await self.send_private_vote_menu(bot, game_id, player_id)
            if not ok:
                continue

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
            if self._is_day_blocked(voter, game):
                return False, "Kezuvchi sabab bugun ovoz bera olmaysiz."
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
            if self._is_day_blocked(voter, game):
                return False, "Kezuvchi sabab bugun ovoz bera olmaysiz."
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
        sent = await self._safe_send_message(
            bot,
            voter_id,
            "🗳 <b>Ovoz berish</b>\n\nKimni kunduzgi yig'ilishda osamiz?",
            reply_markup=vote_keyboard(game_id, choices),
        )
        if sent is None:
            return False, "Ovoz berish ro'yxati yuborilmadi."
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
                await self._safe_send_message(
                    bot,
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
                await self._safe_send_message(bot, chat_id, "Aholi janjallashib uylariga tarqashdi.")
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
            confirm_message = await self._safe_send_message(
                bot,
                chat_id,
                f"Rostdan xam {self._tg_mention(target.telegram_id, target.display_name)}ni osmoqchimisiz?",
                reply_markup=confirm_hang_keyboard(game_id, target.telegram_id),
            )
            scheduler.add_job(
                self.resolve_hang_confirmation,
                "date",
                run_date=datetime.now(timezone.utc) + timedelta(seconds=30),
                args=[bot, game_id, target.telegram_id, confirm_message.message_id if confirm_message else None],
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
        active_ids.update(
            player.telegram_id
            for player in alive_after_vote
            if self._is_day_blocked(player, game)
        )
        for player in alive_after_vote:
            if player.telegram_id in active_ids:
                player.inactive_rounds = 0
            else:
                player.inactive_rounds = (player.inactive_rounds or 0) + 1
        await session.commit()

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
            if self._is_day_blocked(voter, game):
                return False, "Kezuvchi sabab bugun osish bo'yicha ovoz bera olmaysiz.", None
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
            vote_changed = False
            if existing:
                vote_changed = existing.approve != confirmed or existing.target_telegram_id != target_id
                existing.target_telegram_id = target_id
                existing.approve = confirmed
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
                changed=vote_changed,
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
                    "Ovozingiz yangilandi." if vote_changed else "Siz ovoz berdingiz.",
                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                )
            except TelegramForbiddenError:
                pass
            return True, "Ovozingiz yangilandi." if vote_changed else "Ovozingiz qabul qilindi.", keyboard

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
            if self._is_day_blocked(judge, game):
                return False, "Kezuvchi sabab bugun hech qanday amal bajara olmaysiz."
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
            await self._safe_edit_message_reply_markup(
                bot,
                chat_id=chat_id,
                message_id=confirm_message_id,
                reply_markup=None,
            )
        await self._safe_send_message(
            bot,
            chat_id,
            "🧑‍⚖️ Sudya  kunduzgi hukmni bekor qildi.\n"
            "Hukm bekor qilindi. Aholi tarqaldi...",
        )
        await self._safe_send_message(
            bot,
            judge_id,
            f"Siz {target.display_name} uchun kunduzgi hukmni bekor qildingiz.",
            reply_markup=await self.group_return_keyboard(bot, chat_id),
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
                await self._safe_edit_message_reply_markup(
                    bot,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=None,
                )

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
                    await self._safe_send_message(
                        bot,
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
                    if Role(target.role) == Role.JESTER:
                        target.won = True
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
                    await self._safe_send_message(bot, chat_id, vote_text)

                    if Role(target.role) == Role.JESTER:
                        await self._safe_send_message(
                            bot,
                            chat_id,
                            f"🎭 Masxaraboz {self._tg_mention(target.telegram_id, target.display_name)} "
                            "o'z xohishiga yetdi va alohida g'olib bo'ldi!"
                        )

                    succession_events = self._apply_role_successions(
                        (
                            await session.execute(
                                select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.id.asc())
                            )
                        ).scalars().all(),
                        {target.telegram_id},
                    )
                    if succession_events:
                        await session.commit()
                        for line, heir_id, new_role in succession_events:
                            await self._safe_send_message(bot, chat_id, line)
                            try:
                                await bot.send_message(
                                    heir_id,
                                    self._private_role_text(new_role),
                                    reply_markup=await self.group_return_keyboard(bot, chat_id),
                                )
                            except TelegramForbiddenError:
                                pass

                    if Role(target.role) == Role.SORCERER:
                        extra_day_dead = set()
                        alive_now = await self._alive_players(session, game_id)
                        candidates = [(p.telegram_id, p.display_name) for p in alive_now if p.telegram_id != target.telegram_id]
                        if candidates:
                            await session.commit()
                            await self._safe_send_message(
                                bot,
                                target.telegram_id,
                                "🧞‍♂️ Siz osildingiz. Endi o'zingiz bilan birga kimni olib ketishni tanlang:",
                                reply_markup=sorcerer_hang_revenge_keyboard(
                                    game_id=game_id,
                                    sorcerer_id=target.telegram_id,
                                    choices=candidates,
                                ),
                            )
                            await self._safe_send_message(
                                bot,
                                chat_id,
                                f"🧞‍♂️ {self._tg_mention(target.telegram_id, target.display_name)} qasos uchun nishon tanlayapti...",
                            )
                    else:
                        extra_day_dead = set()

                    all_players = (
                        await session.execute(
                            select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.id.asc())
                        )
                    ).scalars().all()
                    extra_successions = self._apply_role_successions(all_players, extra_day_dead)
                    if extra_successions:
                        await session.commit()
                    for line, heir_id, new_role in extra_successions:
                        await self._safe_send_message(bot, chat_id, line)
                        try:
                            await bot.send_message(
                                heir_id,
                                self._private_role_text(new_role),
                                reply_markup=await self.group_return_keyboard(bot, chat_id),
                            )
                        except TelegramForbiddenError:
                            pass
            else:
                self._add_game_log(
                    session,
                    game,
                    "hang_rejected",
                    target=target,
                    yes_confirm=yes_confirm,
                    no_confirm=no_confirm,
                )
                await self._safe_send_message(bot, chat_id, "Aholi janjallashib uylariga tarqashdi.")

            await self._apply_inactivity_after_vote(bot, session, game, votes)
            winner = await self.check_winner(game_id)
            if winner:
                await self.finish_game(bot, game_id, winner)
                return

            game.phase = GamePhase.NIGHT.value
            game.night_number += 1
            await session.commit()

        await self.start_night(bot, game_id)

    async def resolve_sorcerer_hang_revenge(
        self,
        bot: Bot,
        game_id: int,
        sorcerer_id: int,
        target_id: int,
    ) -> tuple[bool, str]:
        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value:
                return False, t(self.settings.default_language, "callback_expired")

            sorcerer = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == sorcerer_id,
                    )
                )
            ).scalar_one_or_none()
            if sorcerer is None or Role(sorcerer.role) != Role.SORCERER or sorcerer.alive:
                return False, "Bu amal hozir mavjud emas."
            if sorcerer.sorcerer_revenge_used:
                return False, "Qasos allaqachon ishlatilgan."

            target = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == target_id,
                        GamePlayer.alive.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if target is None or target.telegram_id == sorcerer_id:
                return False, "Nishon noto'g'ri."

            target.alive = False
            target.death_day = game.day_number
            sorcerer.sorcerer_revenge_used = True
            self._add_game_log(
                session,
                game,
                "sorcerer_revenge_after_hang",
                actor=sorcerer,
                target=target,
            )
            await session.commit()

            await self._safe_send_message(
                bot,
                game.chat_id,
                f"💣 Afsungar afsun qildi va {self._tg_mention(target.telegram_id, target.display_name)}ni jahannamga olib ketdi!\n\n"
                f"U edi {role_label(target.role)}",
            )

            all_players = (
                await session.execute(
                    select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.id.asc())
                )
            ).scalars().all()
            extra_successions = self._apply_role_successions(all_players, {target.telegram_id})
            if extra_successions:
                await session.commit()
            for line, heir_id, new_role in extra_successions:
                await self._safe_send_message(bot, game.chat_id, line)
                try:
                    await bot.send_message(
                        heir_id,
                        self._private_role_text(new_role),
                        reply_markup=await self.group_return_keyboard(bot, game.chat_id),
                    )
                except TelegramForbiddenError:
                    pass

            winner = await self.check_winner(game_id)
            if winner:
                await self.finish_game(bot, game_id, winner)
            else:
                await session.commit()
        return True, "Qasos bajarildi."

    async def resolve_sorcerer_judgement(
        self,
        bot: Bot,
        game_id: int,
        sorcerer_id: int,
        attacker_id: int,
        action: str,
    ) -> tuple[bool, str]:
        if action not in {"forgive", "kill"}:
            return False, "Noma'lum amal."

        pending_key = (game_id, sorcerer_id, attacker_id)
        pending = self._pending_sorcerer_judgements.get(pending_key)
        if pending is None:
            return False, "Bu tanlov eskirgan yoki allaqachon qabul qilingan."
        expires_at, attacker_role_name = pending
        if expires_at <= self._monotonic():
            self._pending_sorcerer_judgements.pop(pending_key, None)
            return False, "Bu tanlov muddati tugagan."

        async with self.session_factory() as session:
            game = (await session.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
            if game is None or game.status != GameStatus.ACTIVE.value:
                return False, t(self.settings.default_language, "callback_expired")

            sorcerer = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == sorcerer_id,
                    )
                )
            ).scalar_one_or_none()
            attacker = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game_id,
                        GamePlayer.telegram_id == attacker_id,
                    )
                )
            ).scalar_one_or_none()
            if sorcerer is None or Role(sorcerer.role) != Role.MAQ:
                self._pending_sorcerer_judgements.pop(pending_key, None)
                return False, "Bu tanlov bekor bo'lgan."
            if attacker is None:
                self._pending_sorcerer_judgements.pop(pending_key, None)
                return False, "Nishon topilmadi."

            chat_id = game.chat_id
            self._pending_sorcerer_judgements.pop(pending_key, None)

            if action == "forgive":
                await self._safe_send_message(
                    bot,
                    chat_id,
                    f"🕊 Sehrgar {role_label(attacker.role)} xatosini kechirdi.",
                )
                return True, "Kechirildi."

            if not attacker.alive:
                await self._safe_send_message(
                    bot,
                    chat_id,
                    f"💀 Sehrgar {self._tg_mention(attacker.telegram_id, attacker.display_name)} "
                    f"({role_label(attacker.role)}) xatosini kechirmadi, lekin u allaqachon o'lgan edi.",
                )
                return True, "Nishon allaqachon o'lgan."

            attacker.alive = False
            attacker.death_day = game.day_number
            await session.commit()

            await self._safe_send_message(
                bot,
                chat_id,
                f"💀 Sehrgar {self._tg_mention(attacker.telegram_id, attacker.display_name)} "
                f"({role_label(attacker.role)}) xatosini kechirmadi va oldirdi!",
            )

            all_players = (
                await session.execute(
                    select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.id.asc())
                )
            ).scalars().all()
            extra_successions = self._apply_role_successions(all_players, {attacker.telegram_id})
            if extra_successions:
                await session.commit()
            for line, heir_id, new_role in extra_successions:
                await self._safe_send_message(bot, chat_id, line)
                try:
                    await bot.send_message(
                        heir_id,
                        self._private_role_text(new_role),
                        reply_markup=await self.group_return_keyboard(bot, chat_id),
                    )
                except TelegramForbiddenError:
                    pass

            winner = await self.check_winner(game_id)
            if winner:
                await self.finish_game(bot, game_id, winner)
            return True, "Oldirish bajarildi."

    async def check_winner(self, game_id: int) -> Optional[Team]:
        """
        Oyin xulosasini tekshiradi - kim yutdi?
        
        Qoida:
        1. Mafia nol qolsa → CITY wins
        2. Qotil o'z qolsa → KILLER wins
        3. Suidsid o'ldirilib ketsa → NEUTRAL wins
        4. 2 kishi qolsa va 1 ta mafia bo'lsa → MAFIA wins (final duel)
        5. Mafia soni ≥ non-mafia soni bo'lsa → MAFIA wins
        6. Boshqa holda oyni davom ettir
        """
        async with self.session_factory() as session:
            alive = (
                await session.execute(select(GamePlayer).where(GamePlayer.game_id == game_id, GamePlayer.alive.is_(True)))
            ).scalars().all()
            
            if not alive:
                return Team.CITY

            teams = [p.team for p in alive]
            
            mafia_count = sum(1 for t in teams if t == Team.MAFIA.value)
            killer_count = sum(1 for t in teams if t == Team.KILLER.value)
            neutral_count = sum(1 for t in teams if t == Team.NEUTRAL.value)
            city_count = len(alive) - mafia_count - killer_count - neutral_count
            
            singleton_count = killer_count + neutral_count

            # Mafia yo'q va singleton shaharni yenga olmaydigan holat: tirik shahar tomoni yutadi.
            if mafia_count == 0 and (singleton_count == 0 or city_count >= 2):
                return Team.CITY
            
            # Qotil o'z qolsa → Qotil yutadi
            if len(alive) == 1 and alive[0].team == Team.KILLER.value:
                return Team.KILLER
            
            # Suidsid o'ldirilib ketsa → Suidsid yutadi (lekin bu bajarilgan deb hisob qilamiz)
            if len(alive) == 1 and alive[0].team == Team.NEUTRAL.value:
                return Team.NEUTRAL
            
            # Final duel: 2 kishi qolsa va 1 ta mafia bo'lsa → Mafia wins
            # (Don/Mafia vs. istalgan boshqa)
            if len(alive) == 2 and mafia_count == 1 and killer_count == 0:
                return Team.MAFIA

            # 1 ta shahar + 1 ta mafia + 1 ta singleton bo'lsa o'yin davom etadi.
            if city_count == 1 and mafia_count == 1 and singleton_count == 1:
                return None

            # 2 ta mafia + 1 ta neutral bo'lsa (qotil yo'q) mafia tomoni yutadi.
            if city_count == 0 and mafia_count >= 2 and neutral_count == 1 and killer_count == 0:
                return Team.MAFIA
            
            # Asosiy qaror: Mafia soni ≥ city + neutral soni bo'lsa → Mafia yutadi
            # (Qotil hisob bo'lmaydi, u o'zining o'yinini o'ynaydi)
            non_mafia_fighting = city_count + neutral_count
            if mafia_count > 0 and mafia_count >= non_mafia_fighting and killer_count == 0:
                return Team.MAFIA
            
            # O'yin davom ettirish
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
            reward_by_user: dict[int, tuple[int, int, bool]] = {}
            news_bonus_ids = await self.news_bonus_subscriber_ids(bot, [p.telegram_id for p in players])
            arsonist_forced_win = any(
                Role(p.role) == Role.ARSONIST and bool(p.won)
                for p in players
                if p.role is not None
            )
            for p in players:
                # O'yin davomida o'lganlar yakunda mag'lub hisoblanadi.
                # Faqat Suidsid kabi alohida shart bilan yutgan rollar p.won orqali g'olib bo'lib qoladi.
                is_winner = bool(p.won) or (p.team == winner_team.value and p.alive)
                if p.role == Role.MINER.value and p.alive:
                    is_winner = True
                # Afsungar faqat maxsus qasos yutug'i belgilangan bo'lsa g'olib bo'ladi.
                if p.role == Role.SORCERER.value:
                    is_winner = bool(p.won)
                if arsonist_forced_win:
                    is_winner = bool(p.won)
                # O'yindan ixtiyoriy chiqib ketganlar yutmaydi.
                if p.left_game:
                    is_winner = False
                p.won = is_winner
                bonus_multiplier = 2 if p.telegram_id in news_bonus_ids else 1
                reward_dollar = (
                    self.settings.winner_reward_dollar
                    if is_winner
                    else self.settings.loser_reward_dollar
                ) * bonus_multiplier
                reward_diamond = (
                    self.settings.winner_reward_diamond
                    if is_winner
                    else self.settings.loser_reward_diamond
                ) * bonus_multiplier
                reward_by_user[p.telegram_id] = (
                    reward_dollar,
                    reward_diamond,
                    bonus_multiplier == 2,
                )
                user = users.get(p.telegram_id)
                if user:
                    user.total_games += 1
                    bonus_note = " (kanal 2x bonus)" if bonus_multiplier == 2 else ""
                    if is_winner:
                        user.wins += 1
                        user.dollar += reward_dollar
                        user.diamonds += reward_diamond
                        self._record_dollar_transaction(
                            session,
                            user,
                            reward_dollar,
                            "game_winner_reward",
                            note=f"O'yin #{game.id}: g'olib mukofoti{bonus_note}",
                            chat_id=game.chat_id,
                        )
                        self._record_diamond_transaction(
                            session,
                            user,
                            reward_diamond,
                            "game_winner_reward",
                            note=f"O'yin #{game.id}: g'olib mukofoti{bonus_note}",
                            chat_id=game.chat_id,
                        )
                    else:
                        user.dollar += reward_dollar
                        user.diamonds += reward_diamond
                        self._record_dollar_transaction(
                            session,
                            user,
                            reward_dollar,
                            "game_participation_reward",
                            note=f"O'yin #{game.id}: ishtirok mukofoti{bonus_note}",
                            chat_id=game.chat_id,
                        )
                        self._record_diamond_transaction(
                            session,
                            user,
                            reward_diamond,
                            "game_participation_reward",
                            note=f"O'yin #{game.id}: ishtirok mukofoti{bonus_note}",
                            chat_id=game.chat_id,
                        )
                (winners if is_winner else losers).append(p)

            self._add_game_log(
                session,
                game,
                "game_finished",
                winner_team=winner_team.value,
                winners=[p.telegram_id for p in winners],
                losers=[p.telegram_id for p in losers],
                news_bonus_ids=sorted(news_bonus_ids),
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
        loser_start = len(winner_lines) + 1
        loser_lines = [
            f"{idx}. {self._tg_mention(p.telegram_id, p.display_name)} - {role_label(p.role)}"
            for idx, p in enumerate(losers, loser_start)
        ]
        winners_block = "\n".join(winner_lines) if winner_lines else "-"
        losers_block = "\n".join(loser_lines) if loser_lines else "-"

        news_channel = self._news_bonus_channel_id()
        bonus_hint = f"\n\n📰 <i>{news_channel} kanaliga obuna bo'ling va 2x mukofot oling!</i>"

        text = (
            "<b>O'yin tugadi!</b>\n\n"
            "G'oliblar:\n"
            f"{winners_block}\n\n"
            "Mag'lublar:\n"
            f"{losers_block}\n\n"
            f"O'yin: {self._format_duration(duration_seconds)} davom etdi"
            f"{bonus_hint}"
        )
        await bot.send_message(chat_id, text)

        async with self.session_factory() as session:
            users = {
                u.telegram_id: u
                for u in (
                    await session.execute(select(User).where(User.telegram_id.in_([p.telegram_id for p in players])))
                ).scalars().all()
            }

        player_ids = [p.telegram_id for p in players]
        news_url_task = self.get_news_channel_url()
        hero_tasks = {pid: self.user_has_hero(pid) for pid in player_ids}
        news_url, *hero_results = await asyncio.gather(
            news_url_task, *(hero_tasks[pid] for pid in player_ids)
        )
        hero_map = dict(zip(player_ids, hero_results))

        for p in players:
            user = users.get(p.telegram_id)
            if user is None:
                continue
            result_title = "you_win" if p.won else "you_lose"
            reward_dollar, reward_diamond, used_news_bonus = reward_by_user.get(
                p.telegram_id,
                (
                    self.settings.winner_reward_dollar if p.won else self.settings.loser_reward_dollar,
                    self.settings.winner_reward_diamond if p.won else self.settings.loser_reward_diamond,
                    False,
                ),
            )
            bonus_text = "\n📰 Kanal obunasi: <b>2x mukofot berildi!</b>" if used_news_bonus else ""
            body = (
                f"{t(user.language, result_title, dollar=reward_dollar, diamond=reward_diamond)}{bonus_text}\n\n"
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
                        has_hero=hero_map.get(p.telegram_id, False),
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

        def stored_role(value: Optional[str]) -> str:
            if not value:
                return "-"
            try:
                return role_label(Role(value))
            except ValueError:
                return value

        display_name = GameEngine._tg_mention(user.telegram_id, user.display_name)
        return (
            f"👤 Nik: {display_name}\n"
            f"⭐ ID: <code>{user.telegram_id}</code>\n\n"
            f"<tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji> Dollar: <b>{user.dollar}</b>\n"
            f"<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> Olmos: <b>{user.diamonds}</b>\n\n"
            f"🛡 Himoya: <b>{user.protection}</b> {state(user.use_protection)}\n"
            f"🧿 Qotildan himoya: <b>{user.killer_protection}</b> {state(user.use_killer_protection)}\n"
            f"⚖️ Ovoz berishni himoya qilish: <b>{user.vote_protection}</b> {state(user.use_vote_protection)}\n"
            f"💊 Doridan himoya: <b>{user.drug_protection}</b> {state(user.use_drug_protection)}\n"
            f"📦 Sirpanishdan himoya: <b>{user.miner_protection}</b> {state(user.use_miner_protection)}\n"
            "\n"
            f"🎭 Maska: <b>{user.mask}</b> {state(user.use_mask)}\n"
            f"📁 Soxta hujjat: <b>{user.fake_document}</b> {state(user.use_fake_document)}\n"
            f"🃏 Keyingi o'yindagi rolingiz: <b>{stored_role(user.next_game_role)}</b>\n"
            f"🚫 Keyingi o'yinda o'chiriladigan rol: <b>{stored_role(user.next_game_disabled_role)}</b>\n\n"
            f"🎯 Побед: <b>{user.wins}</b>\n"
            f"🎲 Всего игр: <b>{user.total_games}</b>\n\n"
            "@WorldMafiaNews kanaliga obuna bolsangiz sizga 2x mukofot beriladi!"
        )

    @staticmethod
    def format_user_dashboard_entities(user: User) -> dict:
        def state(value: bool) -> str:
            return ""

        def stored_role(value: Optional[str]) -> str:
            if not value:
                return "-"
            try:
                return role_label(Role(value))
            except ValueError:
                return value

        display_name = TextLink(user.display_name or "Unknown", url=f"tg://user?id={user.telegram_id}")
        vip_status = ""
        if user.vip_until:
            now = datetime.now(timezone.utc)
            vip_until = user.vip_until
            if vip_until.tzinfo is None:
                vip_until = vip_until.replace(tzinfo=timezone.utc)
            else:
                vip_until = vip_until.astimezone(timezone.utc)
            if vip_until > now:
                remaining = vip_until - now
                days = remaining.days
                vip_status = f"\n👑 VIP: ✅ ({days} kun qoldi)"
            else:
                vip_status = "\n👑 VIP: ❌ (muddati tugagan)"
        return Text(
            "👤 Nik: ", display_name, "\n",
            "⭐ ID: ", Code(str(user.telegram_id)), "\n\n",
            CustomEmoji("💵", custom_emoji_id=DOLLAR_EMOJI_ID), " Dollar: ", Bold(str(user.dollar)), "\n",
            CustomEmoji("💎", custom_emoji_id=DIAMOND_EMOJI_ID), " Olmos: ", Bold(str(user.diamonds)), vip_status, "\n\n",
            "🛡 Himoya: ", Bold(str(user.protection)), f" {state(user.use_protection)}\n",
            "🧿 Qotildan himoya: ", Bold(str(user.killer_protection)), f" {state(user.use_killer_protection)}\n",
            "⚖️ Ovoz berishni himoya qilish: ", Bold(str(user.vote_protection)), f" {state(user.use_vote_protection)}\n",
            "💊 Doridan himoya: ", Bold(str(user.drug_protection)), f" {state(user.use_drug_protection)}\n",
            "📦 Sirpanishdan himoya: ", Bold(str(user.miner_protection)), f" {state(user.use_miner_protection)}\n",
            "\n",
            "🎭 Maska: ", Bold(str(user.mask)), f" {state(user.use_mask)}\n",
            "📁 Soxta hujjat: ", Bold(str(user.fake_document)), f" {state(user.use_fake_document)}\n",
            "🃏 Keyingi o'yindagi rolingiz: ", Bold(stored_role(user.next_game_role)), "\n",
            "🚫 Keyingi o'yinda o'chiriladigan rol: ", Bold(stored_role(user.next_game_disabled_role)), "\n\n",
            "🎯 Побед: ", Bold(str(user.wins)), "\n",
            "🎲 Всего игр: ", Bold(str(user.total_games)), "\n\n",
            "@WorldMafiaNews kanaliga obuna bolsangiz sizga 2x mukofot beriladi!",
        ).as_kwargs()

    async def user_has_hero(self, telegram_id: int) -> bool:
        async with self.session_factory() as session:
            hero_id = (
                await session.execute(
                    select(Hero.id)
                    .join(User, User.id == Hero.owner_user_id)
                    .where(User.telegram_id == telegram_id)
                )
            ).scalar_one_or_none()
            return hero_id is not None

    @staticmethod
    def _sync_hero_level(hero: Hero) -> None:
        info = hero_level_for_points(int(hero.points or 0))
        hero.level = info.level
        hero.max_defense = HERO_FULL_DEFENSE_PERCENT
        hero.current_defense = min(int(hero.current_defense or 0), HERO_FULL_DEFENSE_PERCENT)
        hero.max_charge = HERO_MAX_CHARGE
        hero.charge = min(int(hero.charge or 0), HERO_MAX_CHARGE)

    @staticmethod
    def _hero_panel_text(hero: Hero) -> str:
        info = hero_level_for_points(int(hero.points or 0))
        next_text = (
            f"{info.next_level} => {info.next_points} ball"
            if info.next_level and info.next_points is not None
            else "Maksimal daraja"
        )
        sale_text = ""
        if hero.is_for_sale:
            sale_text = f"\n🏷 Sotuvda: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {hero.sale_price_diamonds or 0}"
        return (
            "Geroylar haqida\n\n"
            f"🥷 Geroy: {safe_hero_name(hero.name)}\n"
            f"⭐️ Daraja: {info.level}\n"
            f"👊 Kuch: {info.power_text}\n"
            f"🖤 Himoya: {int(hero.current_defense or 0)}%\n"
            f"♥️ Max himoya: {HERO_FULL_DEFENSE_PERCENT}%\n"
            f"🩸 Zaryad miqdori: {int(hero.charge or 0)}\n"
            f"☑️ Jami ballari: {int(hero.points or 0)} ball\n"
            f"⏫ Keyingi daraja = {next_text}{sale_text}\n\n"
            "🛒 Xaridlar uchun:\n"
            f"➕ 1000 Ball qo'shish = <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {HERO_ADD_POINTS_PRICE_DIAMONDS}\n"
            f"🛡 Himoyani to'liq yangilash ({HERO_FULL_DEFENSE_PERCENT}%) = 💶 {HERO_UPGRADE_DEFENSE_PRICE_DOLLAR}\n"
            f"🩸 Qurolni zaryadlash = 💶 {HERO_RECHARGE_PRICE_DOLLAR}\n"
            f"🖋 Geroy nomini o'zgertirish = 💶 {HERO_RENAME_PRICE_DOLLAR}"
        )

    async def hero_panel_data(self, telegram_id: int) -> tuple[bool, str, bool]:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(Hero)
                    .join(User, User.id == Hero.owner_user_id)
                    .where(User.telegram_id == telegram_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return False, "❌ Sizda hali geroy yo'q. Do'kondan <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> 100 almazga sotib olishingiz mumkin.", False
            self._sync_hero_level(row)
            await session.commit()
            return True, self._hero_panel_text(row), bool(row.is_for_sale)

    async def buy_hero(self, telegram_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            user = (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
            if user is None:
                return False, "Avval /start bosing."
            existing = (await session.execute(select(Hero.id).where(Hero.owner_user_id == user.id))).scalar_one_or_none()
            if existing is not None:
                return False, "Sizda allaqachon geroy bor."
            if int(user.diamonds or 0) < HERO_BUY_PRICE_DIAMONDS:
                return False, f"❌ Almaz yetarli emas. Kerak: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {HERO_BUY_PRICE_DIAMONDS}, Sizda: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {user.diamonds or 0}"
            user.diamonds -= HERO_BUY_PRICE_DIAMONDS
            self._record_diamond_transaction(
                session,
                user,
                -HERO_BUY_PRICE_DIAMONDS,
                "hero_buy",
                note="Geroy sotib olish",
            )
            hero = Hero(
                owner_user_id=user.id,
                name=HERO_DEFAULT_NAME,
                points=0,
                level=1,
                current_defense=0,
                max_defense=HERO_FULL_DEFENSE_PERCENT,
                charge=HERO_DEFAULT_CHARGE,
                max_charge=HERO_MAX_CHARGE,
            )
            session.add(hero)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False, "Sizda allaqachon geroy bor."
        return True, "✅ Tabriklaymiz! Siz 🥷 Geroy sotib oldingiz."

    async def hero_add_points(self, telegram_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            user, hero = await self._hero_owner_row(session, telegram_id)
            if user is None or hero is None:
                return False, "❌ Sizda hali geroy yo'q. Do'kondan <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> 100 almazga sotib olishingiz mumkin."
            if int(user.diamonds or 0) < HERO_ADD_POINTS_PRICE_DIAMONDS:
                return False, f"❌ Almaz yetarli emas. Kerak: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {HERO_ADD_POINTS_PRICE_DIAMONDS}, Sizda: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {user.diamonds or 0}"
            old_level = int(hero.level or 1)
            user.diamonds -= HERO_ADD_POINTS_PRICE_DIAMONDS
            self._record_diamond_transaction(
                session,
                user,
                -HERO_ADD_POINTS_PRICE_DIAMONDS,
                "hero_add_points",
                note=f"Geroyga +{HERO_ADD_POINTS_AMOUNT} ball qo'shish",
            )
            hero.points = int(hero.points or 0) + HERO_ADD_POINTS_AMOUNT
            self._sync_hero_level(hero)
            await session.commit()
            level_line = f"\n⭐️ Daraja oshdi: {old_level} → {hero.level}" if hero.level != old_level else ""
            return True, f"✅ +{HERO_ADD_POINTS_AMOUNT} ball qo'shildi. Jami: {hero.points} ball.{level_line}"

    async def hero_upgrade_defense(self, telegram_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            user, hero = await self._hero_owner_row(session, telegram_id)
            if user is None or hero is None:
                return False, "❌ Sizda hali geroy yo'q."
            self._sync_hero_level(hero)
            if int(hero.current_defense or 0) >= HERO_FULL_DEFENSE_PERCENT:
                return False, "Himoya maksimal."
            if int(user.dollar or 0) < HERO_UPGRADE_DEFENSE_PRICE_DOLLAR:
                return False, f"❌ Mablag' yetarli emas. Kerak: 💶 {HERO_UPGRADE_DEFENSE_PRICE_DOLLAR}, Sizda: 💶 {user.dollar or 0}"
            user.dollar -= HERO_UPGRADE_DEFENSE_PRICE_DOLLAR
            hero.max_defense = HERO_FULL_DEFENSE_PERCENT
            hero.current_defense = HERO_FULL_DEFENSE_PERCENT
            await session.commit()
            return True, f"🛡 Himoya to'liq yangilandi: 🖤 {hero.current_defense}/{HERO_FULL_DEFENSE_PERCENT}%"

    async def hero_recharge(self, telegram_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            user, hero = await self._hero_owner_row(session, telegram_id)
            if user is None or hero is None:
                return False, "❌ Sizda hali geroy yo'q."
            if int(hero.charge or 0) >= HERO_MAX_CHARGE:
                return False, "Qurol zaryadi to'liq."
            if int(user.dollar or 0) < HERO_RECHARGE_PRICE_DOLLAR:
                return False, f"❌ Mablag' yetarli emas. Kerak: 💶 {HERO_RECHARGE_PRICE_DOLLAR}, Sizda: 💶 {user.dollar or 0}"
            user.dollar -= HERO_RECHARGE_PRICE_DOLLAR
            hero.charge = HERO_MAX_CHARGE
            hero.max_charge = HERO_MAX_CHARGE
            await session.commit()
            return True, "🩸 Qurol zaryadi to'liq 10 ga qaytarildi."

    async def hero_rename(self, telegram_id: int, raw_name: str) -> tuple[bool, str]:
        ok, name_or_error = sanitize_hero_name(raw_name)
        if not ok:
            return False, name_or_error
        async with self.session_factory() as session:
            user, hero = await self._hero_owner_row(session, telegram_id)
            if user is None or hero is None:
                return False, "❌ Sizda hali geroy yo'q."
            if int(user.dollar or 0) < HERO_RENAME_PRICE_DOLLAR:
                return False, f"❌ Mablag' yetarli emas. Kerak: 💶 {HERO_RENAME_PRICE_DOLLAR}, Sizda: 💶 {user.dollar or 0}"
            hero.name = name_or_error
            user.dollar -= HERO_RENAME_PRICE_DOLLAR
            await session.commit()
        return True, f"✅ Geroy nomi yangilandi: {safe_hero_name(name_or_error)}"

    async def _hero_owner_row(self, session: AsyncSession, telegram_id: int) -> tuple[Optional[User], Optional[Hero]]:
        user = (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
        if user is None:
            return None, None
        hero = (await session.execute(select(Hero).where(Hero.owner_user_id == user.id))).scalar_one_or_none()
        return user, hero

    async def get_hero_market_channel_id(self) -> Optional[str]:
        async with self.session_factory() as session:
            setting = (
                await session.execute(select(BotSetting).where(BotSetting.key == HERO_MARKET_CHANNEL_KEY))
            ).scalar_one_or_none()
            value = (setting.value if setting else "").strip()
            return value or None

    async def set_hero_market_channel(self, bot: Bot, raw_channel: str) -> tuple[bool, str]:
        channel = (raw_channel or "").strip()
        if not channel:
            return False, "Kanal ID yoki @username yuboring."
        if not (channel.startswith("@") or channel.startswith("-100") or channel.lstrip("-").isdigit()):
            return False, "Kanal @username yoki kanal ID bo'lishi kerak."
        try:
            chat = await bot.get_chat(channel)
            me = await bot.get_me()
            member = await bot.get_chat_member(chat.id, me.id)
        except Exception as exc:
            return False, f"❌ Kanal topilmadi yoki bot kira olmaydi: {exc}"
        if member.status not in {"administrator", "creator"}:
            return False, "❌ Bot o'sha kanalda admin bo'lishi kerak."
        value = str(chat.id)
        async with self.session_factory() as session:
            setting = (
                await session.execute(select(BotSetting).where(BotSetting.key == HERO_MARKET_CHANNEL_KEY))
            ).scalar_one_or_none()
            if setting is None:
                session.add(BotSetting(key=HERO_MARKET_CHANNEL_KEY, value=value))
            else:
                setting.value = value
            await session.commit()
        return True, f"✅ Geroy savdo kanali ulandi: <code>{value}</code>"

    async def clear_hero_market_channel(self) -> str:
        async with self.session_factory() as session:
            setting = (
                await session.execute(select(BotSetting).where(BotSetting.key == HERO_MARKET_CHANNEL_KEY))
            ).scalar_one_or_none()
            if setting is None:
                session.add(BotSetting(key=HERO_MARKET_CHANNEL_KEY, value=""))
            else:
                setting.value = ""
            await session.commit()
        return "✅ Geroy savdo kanali o'chirildi."

    def _hero_market_text(self, hero: Hero) -> str:
        info = hero_level_for_points(int(hero.points or 0))
        return (
            "🥷 <b>GEROY SOTUVDA!</b>\n\n"
            f"🥷 Geroy: {safe_hero_name(hero.name)}\n"
            f"⭐️ Daraja: {info.level}\n"
            f"👊 Kuch: {info.power_text}\n"
            f"🖤 Himoya: {int(hero.current_defense or 0)}%\n"
            f"♥️ Max himoya: {HERO_FULL_DEFENSE_PERCENT}%\n"
            f"🩸 Zaryad miqdori: {int(hero.charge or 0)}\n"
            f"☑️ Jami ballari: {int(hero.points or 0)} ball\n\n"
            f"<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> Narxi: {int(hero.sale_price_diamonds or 0)} almaz"
        )

    async def hero_put_for_sale(self, bot: Bot, telegram_id: int, price: int) -> tuple[bool, str]:
        if price < 1 or price > 1_000_000:
            return False, "Narx 1 dan 1 000 000 almazgacha bo'lishi kerak."
        channel_id = await self.get_hero_market_channel_id()
        if not channel_id:
            return False, "❌ Geroy savdo kanali hali admin tomonidan ulanmagan."
        async with self.session_factory() as session:
            _, hero = await self._hero_owner_row(session, telegram_id)
            if hero is None:
                return False, "❌ Sizda hali geroy yo'q."
            hero.is_for_sale = True
            hero.sale_price_diamonds = price
            self._sync_hero_level(hero)
            await session.commit()
            hero_id = hero.id
            text = self._hero_market_text(hero)
        try:
            sent = await bot.send_message(channel_id, text, reply_markup=hero_market_buy_keyboard(hero_id))
        except Exception as exc:
            async with self.session_factory() as session:
                hero = (await session.execute(select(Hero).where(Hero.id == hero_id))).scalar_one_or_none()
                if hero:
                    hero.is_for_sale = False
                    hero.sale_price_diamonds = None
                    await session.commit()
            return False, f"❌ Kanalga post yuborilmadi: {exc}"
        async with self.session_factory() as session:
            hero = (await session.execute(select(Hero).where(Hero.id == hero_id))).scalar_one_or_none()
            if hero:
                hero.sale_channel_message_id = sent.message_id
                await session.commit()
        return True, f"✅ Geroyingiz sotuvga qo'yildi. Narx: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {price}"

    async def hero_cancel_sale(self, bot: Bot, telegram_id: int) -> tuple[bool, str]:
        channel_id = await self.get_hero_market_channel_id()
        async with self.session_factory() as session:
            user, hero = await self._hero_owner_row(session, telegram_id)
            if user is None or hero is None:
                return False, "❌ Sizda hali geroy yo'q."
            if not hero.is_for_sale:
                return False, "Geroy sotuvda emas."
            if int(user.diamonds or 0) < HERO_CANCEL_SALE_PRICE_DIAMONDS:
                return False, "❌ Sotuvdan qaytarish uchun <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> 1 almaz kerak."
            user.diamonds -= HERO_CANCEL_SALE_PRICE_DIAMONDS
            self._record_diamond_transaction(
                session,
                user,
                -HERO_CANCEL_SALE_PRICE_DIAMONDS,
                "hero_sale_cancel",
                note="Geroyni sotuvdan qaytarish",
            )
            message_id = hero.sale_channel_message_id
            hero.is_for_sale = False
            hero.sale_price_diamonds = None
            hero.sale_channel_message_id = None
            await session.commit()
        if channel_id and message_id:
            try:
                await bot.edit_message_text("❌ Geroy sotuvdan olindi.", chat_id=channel_id, message_id=message_id)
            except Exception:
                pass
        return True, "✅ Geroy sotuvdan qaytarildi. Xizmat narxi: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> 1 almaz."

    async def hero_update_sale_price(self, bot: Bot, telegram_id: int, price: int) -> tuple[bool, str]:
        if price < 1 or price > 1_000_000:
            return False, "Narx 1 dan 1 000 000 almazgacha bo'lishi kerak."
        channel_id = await self.get_hero_market_channel_id()
        async with self.session_factory() as session:
            _, hero = await self._hero_owner_row(session, telegram_id)
            if hero is None:
                return False, "❌ Sizda hali geroy yo'q."
            if not hero.is_for_sale:
                return False, "Geroy sotuvda emas."
            hero.sale_price_diamonds = price
            message_id = hero.sale_channel_message_id
            text = self._hero_market_text(hero)
            hero_id = hero.id
            await session.commit()
        if channel_id and message_id:
            try:
                await bot.edit_message_text(text, chat_id=channel_id, message_id=message_id, reply_markup=hero_market_buy_keyboard(hero_id))
            except Exception:
                pass
        return True, f"✅ Geroy narxi yangilandi: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {price}"

    async def hero_market_buy(self, bot: Bot, buyer_telegram_id: int, hero_id: int) -> tuple[bool, str]:
        channel_id = await self.get_hero_market_channel_id()
        seller_telegram_id: Optional[int] = None
        buyer_text = "✅ Siz geroyni sotib oldingiz."
        seller_text = ""
        message_id: Optional[int] = None
        async with self.session_factory() as session:
            buyer = (await session.execute(select(User).where(User.telegram_id == buyer_telegram_id))).scalar_one_or_none()
            if buyer is None:
                return False, "Avval /start bosing."
            if (await session.execute(select(Hero.id).where(Hero.owner_user_id == buyer.id))).scalar_one_or_none() is not None:
                return False, "❌ Avval o'z geroyingizni soting yoki sotuvdan chiqaring."
            hero = (
                await session.execute(select(Hero).where(Hero.id == hero_id).with_for_update())
            ).scalar_one_or_none()
            if hero is None or not hero.is_for_sale or not hero.sale_price_diamonds:
                return False, "Geroy sotuvda emas yoki allaqachon sotilgan."
            seller = (await session.execute(select(User).where(User.id == hero.owner_user_id))).scalar_one_or_none()
            if seller is None:
                return False, "Sotuvchi topilmadi."
            if seller.telegram_id == buyer_telegram_id:
                return False, "O'z geroyingizni sotib ololmaysiz."
            price = int(hero.sale_price_diamonds or 0)
            if int(buyer.diamonds or 0) < price:
                return False, f"❌ Almaz yetarli emas. Kerak: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {price}, Sizda: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {buyer.diamonds or 0}"
            buyer.diamonds -= price
            seller.diamonds += price
            self._record_diamond_transaction(
                session,
                buyer,
                -price,
                "hero_market_buy",
                note=f"Geroy #{hero.id} sotib olindi",
                counterparty=seller,
            )
            self._record_diamond_transaction(
                session,
                seller,
                price,
                "hero_market_sale",
                note=f"Geroy #{hero.id} sotildi",
                counterparty=buyer,
            )
            seller_telegram_id = seller.telegram_id
            seller_text = f"✅ Geroyingiz sotildi. Hisobingizga <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {price} almaz qo'shildi."
            message_id = hero.sale_channel_message_id
            hero.owner_user_id = buyer.id
            hero.is_for_sale = False
            hero.sale_price_diamonds = None
            hero.sale_channel_message_id = None
            await session.commit()
        if channel_id and message_id:
            try:
                await bot.edit_message_text("✅ <b>SOTILDI</b>", chat_id=channel_id, message_id=message_id)
            except Exception:
                pass
        if seller_telegram_id:
            try:
                await bot.send_message(seller_telegram_id, seller_text)
            except Exception:
                pass
        return True, buyer_text

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

    async def _hero_game_context(
        self,
        session: AsyncSession,
        telegram_id: int,
        *,
        require_charge: bool = False,
    ) -> tuple[bool, str, Optional[Game], Optional[GamePlayer], Optional[User], Optional[Hero]]:
        row = (
            await session.execute(
                select(Game, GamePlayer, User, Hero)
                .join(GamePlayer, GamePlayer.game_id == Game.id)
                .join(User, User.telegram_id == GamePlayer.telegram_id)
                .join(Hero, Hero.owner_user_id == User.id)
                .where(
                    Game.status == GameStatus.ACTIVE.value,
                    GamePlayer.telegram_id == telegram_id,
                )
                .order_by(Game.id.desc())
            )
        ).first()
        if row is None:
            return False, "❌ Siz hozir aktiv o'yinda emassiz.", None, None, None, None
        game, player, user, hero = row
        if game.phase != GamePhase.DAY_DISCUSSION.value:
            if game.phase in {GamePhase.DAY_VOTING.value, GamePhase.DAY_CONFIRM.value}:
                return False, "❌ Geroydan foydalanish vaqti tugagan. Ovoz berish boshlandi.", game, player, user, hero
            return False, "❌ Geroy faqat tong otgandan keyin, ovoz berish boshlanguncha ishlaydi.", game, player, user, hero
        if not player.alive:
            return False, "❌ Siz tirik emassiz.", game, player, user, hero
        if hero.is_for_sale:
            return False, "❌ Sotuvdagi geroy locked. Sotuvdan qaytarmaguncha o'yinda ishlata olmaysiz.", game, player, user, hero
        if require_charge and int(hero.charge or 0) <= 0:
            return False, "❌ Geroy quroli zaryadsiz. Do'kondan zaryadlang.", game, player, user, hero
        self._sync_hero_level(hero)
        return True, "", game, player, user, hero

    async def hero_game_panel_text(self, telegram_id: int) -> tuple[bool, str, bool]:
        async with self.session_factory() as session:
            ok, text, _, player, _, hero = await self._hero_game_context(session, telegram_id)
            if not ok:
                return False, text, False
            can_attack = Role(player.role) in HERO_ATTACK_ROLES
            return True, (
                "🥷 Siz geroyingizdan foydalanishingiz mumkin.\n"
                "Ovoz berish boshlanguncha vaqtingiz bor.\n\n"
                f"🎭 Rol: {role_label(player.role)}\n"
                f"🩸 Zaryad: {int(hero.charge or 0)}/{HERO_MAX_CHARGE}\n"
                f"🖤 Himoya: {int(hero.current_defense or 0)}/{HERO_FULL_DEFENSE_PERCENT}%"
            ), can_attack

    async def hero_game_targets(self, telegram_id: int) -> tuple[bool, str, list[GamePlayer]]:
        async with self.session_factory() as session:
            ok, text, game, player, _, _ = await self._hero_game_context(session, telegram_id, require_charge=True)
            if not ok or game is None or player is None:
                return False, text, []
            if Role(player.role) not in HERO_ATTACK_ROLES:
                return False, "❌ Bu rol geroy bilan zarba bera olmaydi. Faqat himoyalanish mumkin.", []
            targets = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.game_id == game.id,
                        GamePlayer.alive.is_(True),
                        GamePlayer.telegram_id != player.telegram_id,
                    ).order_by(GamePlayer.id.asc())
                )
            ).scalars().all()
            return True, "<tg-emoji emoji-id=\"5431774564348605707\">⚔️</tg-emoji> Kimga zarba berasiz?", targets

    async def hero_game_hp_text(self, telegram_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            ok, text, game, _, _, _ = await self._hero_game_context(session, telegram_id)
            if not ok or game is None:
                return False, text
            players = (
                await session.execute(
                    select(GamePlayer).where(GamePlayer.game_id == game.id).order_by(GamePlayer.id.asc())
                )
            ).scalars().all()
            lines = ["📊 <b>O'yinchilar joni:</b>"]
            for idx, player in enumerate(players, 1):
                mark = " ☠️" if not player.alive or int(player.hero_hp or 0) <= 0 else ""
                lines.append(
                    f"{idx}. {self._tg_mention(player.telegram_id, player.display_name)} — "
                    f"♥️ {int(player.hero_hp or 0)}/{int(player.hero_max_hp or HERO_DEFAULT_HP)}{mark}"
                )
            return True, "\n".join(lines)

    async def hero_game_defend(self, telegram_id: int, amount_raw: str = "max") -> tuple[bool, str]:
        async with self.session_factory() as session:
            ok, text, _, player, _, hero = await self._hero_game_context(session, telegram_id)
            if not ok or player is None or hero is None:
                return False, text
            current = int(hero.current_defense or 0)
            if current <= 0:
                return False, "Himoya mavjud emas. Do'kondan himoyani yangilang."
            amount = current
            player.hero_defense_active = True
            player.hero_defense_amount = amount
            await session.commit()
            return True, f"🛡 Himoya avtomatik to'liq yoqildi: 🖤 {amount}/{HERO_FULL_DEFENSE_PERCENT}%"

    async def hero_damage_prompt(self, telegram_id: int, target_player_id: int) -> tuple[bool, str, bool]:
        async with self.session_factory() as session:
            ok, text, game, player, _, hero = await self._hero_game_context(session, telegram_id, require_charge=True)
            if not ok or game is None or player is None or hero is None:
                return False, text, False
            target = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.id == target_player_id,
                        GamePlayer.game_id == game.id,
                        GamePlayer.alive.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if target is None:
                return False, "Target topilmadi yoki tirik emas.", False
            if target.telegram_id == player.telegram_id:
                return False, "O'zingizni ura olmaysiz.", False
            if Role(player.role) not in HERO_ATTACK_ROLES:
                return False, "❌ Bu rol geroy bilan zarba bera olmaydi. Faqat himoyalanish mumkin.", False
            info = hero_level_for_points(int(hero.points or 0))
            if info.max_hit:
                return True, "<tg-emoji emoji-id=\"5431774564348605707\">⚔️</tg-emoji> Maksimal zarba beriladi.", True
            return True, f"<tg-emoji emoji-id=\"5431774564348605707\">⚔️</tg-emoji> Geroyingiz {info.power_text} oralig'ida random zarba beradi.", False

    async def hero_game_attack(
        self,
        bot: Bot,
        attacker_telegram_id: int,
        target_player_id: int,
        damage_raw: str,
    ) -> tuple[bool, str]:
        async with self.session_factory() as session:
            ok, text, game, attacker, _, hero = await self._hero_game_context(
                session,
                attacker_telegram_id,
                require_charge=True,
            )
            if not ok or game is None or attacker is None or hero is None:
                return False, text
            target = (
                await session.execute(
                    select(GamePlayer).where(
                        GamePlayer.id == target_player_id,
                        GamePlayer.game_id == game.id,
                        GamePlayer.alive.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if target is None:
                return False, "Target topilmadi yoki tirik emas."
            if target.telegram_id == attacker.telegram_id:
                return False, "O'zingizni ura olmaysiz."
            if Role(attacker.role) not in HERO_ATTACK_ROLES:
                return False, "❌ Bu rol geroy bilan zarba bera olmaydi. Faqat himoyalanish mumkin."

            info = hero_level_for_points(int(hero.points or 0))
            target_hp = int(target.hero_hp or HERO_DEFAULT_HP)
            target_defense = int(target.hero_defense_amount or 0) if target.hero_defense_active else 0
            target_hero = None
            if target_defense > 0:
                target_hero = (
                    await session.execute(
                        select(Hero)
                        .join(User, User.id == Hero.owner_user_id)
                        .where(User.telegram_id == target.telegram_id)
                    )
                ).scalar_one_or_none()
            if info.max_hit:
                entered_damage = max(1, target_hp + target_defense)
            else:
                min_power = int(HERO_LEVELS[int(hero.level)]["power_min"])  # type: ignore[index]
                max_power = int(HERO_LEVELS[int(hero.level)]["power_max"])  # type: ignore[index]
                entered_damage = random.randint(min_power, max_power)

            hero.charge = max(0, int(hero.charge or 0) - 1)
            remaining_damage = entered_damage
            if target.hero_defense_active and int(target.hero_defense_amount or 0) > 0:
                absorbed = min(int(target.hero_defense_amount or 0), remaining_damage)
                target.hero_defense_amount = int(target.hero_defense_amount or 0) - absorbed
                if target_hero is not None:
                    target_hero.current_defense = max(0, int(target_hero.current_defense or 0) - absorbed)
                remaining_damage -= absorbed
                if int(target.hero_defense_amount or 0) <= 0:
                    target.hero_defense_active = False
                    target.hero_defense_amount = 0
            if remaining_damage > 0:
                target.hero_hp = max(0, int(target.hero_hp or HERO_DEFAULT_HP) - remaining_damage)

            killed = target.hero_hp <= 0
            kill_text = ""
            succession_events: list[tuple[str, int, Role]] = []
            if killed:
                target.alive = False
                target.killed_by_hero = True
                target.death_day = game.day_number
                if Role(target.role) == Role.SORCERER:
                    target.sorcerer_revenge_used = True
                    target.won = True
                all_players = (
                    await session.execute(
                        select(GamePlayer).where(GamePlayer.game_id == game.id).order_by(GamePlayer.id.asc())
                    )
                ).scalars().all()
                succession_events = self._apply_role_successions(all_players, {target.telegram_id})
                target_name = self._tg_mention(target.telegram_id, target.display_name)
                kill_text = (
                    f"⚰️ {role_label(target.role)} {target_name}ni {role_label(attacker.role)} "
                    "o'zining jasur geroyi bilan yer tishlatdi!"
                )
                self._add_game_log(
                    session,
                    game,
                    "hero_kill",
                    actor_role=attacker.role,
                    target=target,
                    damage=entered_damage,
                )
            else:
                self._add_game_log(
                    session,
                    game,
                    "hero_attack",
                    actor_role=attacker.role,
                    target=target,
                    damage=entered_damage,
                    hp=target.hero_hp,
                )
            await session.commit()
            chat_id = game.chat_id
            target_id = target.telegram_id
            target_hp_after = int(target.hero_hp or 0)
            target_max_hp = int(target.hero_max_hp or HERO_DEFAULT_HP)
            game_id = game.id

        if killed:
            await bot.send_message(chat_id, kill_text)
            for line, heir_id, new_role in succession_events:
                await bot.send_message(chat_id, line)
                try:
                    await bot.send_message(
                        heir_id,
                        self._private_role_text(new_role),
                        reply_markup=await self.group_return_keyboard(bot, chat_id),
                    )
                except TelegramForbiddenError:
                    pass
            winner = await self.check_winner(game_id)
            if winner:
                await self.finish_game(bot, game_id, winner)
            return True, "<tg-emoji emoji-id=\"5431774564348605707\">⚔️</tg-emoji> Zarba berildi. Target o'yindan chetlatildi."
        try:
            await bot.send_message(
                target_id,
                f"💥 Sizga noma'lum geroy tomonidan zarba berildi. Qolgan jon: ♥️ {target_hp_after}/{target_max_hp}",
                reply_markup=await self.group_return_keyboard(bot, chat_id),
            )
        except TelegramForbiddenError:
            pass
        return True, f"<tg-emoji emoji-id=\"5431774564348605707\">⚔️</tg-emoji> Zarba berildi. Target joni: ♥️ {target_hp_after}/{target_max_hp}"

    async def send_hero_phase_prompts(self, bot: Bot, game_id: int) -> None:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(Game, GamePlayer, User, Hero)
                    .join(GamePlayer, GamePlayer.game_id == Game.id)
                    .join(User, User.telegram_id == GamePlayer.telegram_id)
                    .join(Hero, Hero.owner_user_id == User.id)
                    .where(
                        Game.id == game_id,
                        Game.status == GameStatus.ACTIVE.value,
                        Game.phase == GamePhase.DAY_DISCUSSION.value,
                        GamePlayer.alive.is_(True),
                        Hero.is_for_sale.is_(False),
                    )
                )
            ).all()
            chat_id = rows[0][0].chat_id if rows else None
        if not rows:
            return
        sent_any = False
        for _, player, _, _ in rows:
            try:
                await bot.send_message(
                    player.telegram_id,
                    "🥷 Siz geroyingizdan foydalanishingiz mumkin. Ovoz berish boshlanguncha vaqtingiz bor.",
                    reply_markup=hero_game_keyboard(can_attack=Role(player.role) in HERO_ATTACK_ROLES),
                )
                sent_any = True
            except TelegramForbiddenError:
                pass
        if chat_id and sent_any:
            await bot.send_message(chat_id, "🥷 Geroy egalari bot shaxsiy xabaridan foydalanishi mumkin.")

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

    async def check_command_permission(self, bot: Bot, chat_id: int, user_id: int, command_key: str) -> tuple[bool, str]:
        gsm = GroupSettingsManager(self.session_factory)
        level = await gsm.get_command_permission(chat_id, command_key)
        if level == "user":
            return True, ""
        if level == "admin":
            if await self.is_admin_or_creator(bot, chat_id, user_id):
                return True, ""
            return False, "❌ Sizda bu buyruqni ishlatish huquqi yo'q."
        if level == "owner":
            if user_id == self.settings.owner_id:
                return True, ""
            return False, "❌ Sizda bu buyruqni ishlatish huquqi yo'q."
        return True, ""

    async def _get_cached_chat_permission(self, chat_id: int, phase: str) -> str:
        cache_key = (chat_id, phase)
        now = self._monotonic()
        cached = self._chat_permission_cache.get(cache_key)
        if cached:
            expire_time, permission = cached
            if expire_time > now:
                return permission
            del self._chat_permission_cache[cache_key]
        gsm = GroupSettingsManager(self.session_factory)
        permission = await gsm.get_chat_permission(chat_id, phase)
        self._chat_permission_cache[cache_key] = (now + self._chat_permission_cache_ttl, permission)
        if len(self._chat_permission_cache) > self._cache_limit:
            expired_keys = [k for k, v in self._chat_permission_cache.items() if v[0] <= now]
            for k in expired_keys:
                self._chat_permission_cache.pop(k, None)
            if len(self._chat_permission_cache) > self._cache_limit:
                for k in list(self._chat_permission_cache.keys())[: max(1, self._cache_limit // 10)]:
                    self._chat_permission_cache.pop(k, None)
        return permission

    async def check_chat_write_permission(self, bot: Bot, chat_id: int, user_id: int) -> bool:
        if await self.is_vip_user_active(user_id):
            return True
        active = await self.active_game_for_chat(chat_id)
        if active is None or active.status != GameStatus.ACTIVE.value:
            return True
        phase = "night" if active.phase == GamePhase.NIGHT.value else "day"
        permission = await self._get_cached_chat_permission(chat_id, phase)
        if permission == "all":
            return True
        if permission == "owner":
            return user_id == self.settings.owner_id
        if permission == "admin":
            return await self.is_admin_or_creator(bot, chat_id, user_id)
        if permission in ("alive_players", "players"):
            async with self.session_factory() as session:
                player = (
                    await session.execute(
                        select(GamePlayer).where(
                            GamePlayer.game_id == active.id,
                            GamePlayer.telegram_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if player is None:
                    return False
                if permission == "alive_players":
                    return bool(player.alive)
                return True
        return True

    async def is_vip_user_active(self, user_id: int) -> bool:
        async with self.session_factory() as session:
            user = (
                await session.execute(
                    select(User.vip_until).where(User.telegram_id == user_id)
                )
            ).scalar_one_or_none()
        if user is None:
            return False
        vip_until = user
        if vip_until.tzinfo is None:
            vip_until = vip_until.replace(tzinfo=timezone.utc)
        else:
            vip_until = vip_until.astimezone(timezone.utc)
        return vip_until > self._now_utc()

    async def check_weapon_enabled(self, chat_id: int, weapon_key: str) -> bool:
        gsm = GroupSettingsManager(self.session_factory)
        return await gsm.get_weapon_enabled(chat_id, weapon_key)

    async def get_giveaway_settings(self, chat_id: int) -> dict:
        gsm = GroupSettingsManager(self.session_factory)
        gs = await gsm.get_settings(chat_id)
        return {
            "giveaway_diamond": gs.giveaway_diamond,
            "giveaway_protection": gs.giveaway_protection,
        }

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
            self._record_diamond_transaction(
                session,
                sender,
                -amount,
                "transfer_out",
                note="Userga almaz o'tkazma",
                counterparty=receiver,
            )
            self._record_diamond_transaction(
                session,
                receiver,
                amount,
                "transfer_in",
                note="Userdan almaz qabul qilindi",
                counterparty=sender,
            )
            await session.commit()
            return True, "ok"

    @staticmethod
    def _record_dollar_transaction(
        session: AsyncSession,
        user: User,
        amount: int,
        action: str,
        *,
        note: str = "",
        counterparty: Optional[User] = None,
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
                note=(note or None),
                counterparty_telegram_id=counterparty.telegram_id if counterparty else None,
                counterparty_name=(counterparty.display_name or "User")[:255] if counterparty else None,
                chat_id=chat_id,
            )
        )

    async def transfer_dollars(self, from_user_id: int, to_user_id: int, amount: int) -> tuple[bool, str]:
        if amount <= 0:
            return False, "Miqdor musbat bo'lishi kerak."
        async with self.session_factory() as session:
            sender = (await session.execute(select(User).where(User.telegram_id == from_user_id))).scalar_one_or_none()
            receiver = (await session.execute(select(User).where(User.telegram_id == to_user_id))).scalar_one_or_none()
            if sender is None or receiver is None:
                return False, "Foydalanuvchi topilmadi."
            if (sender.dollar or 0) < amount:
                return False, "Balans yetarli emas."
            sender.dollar -= amount
            receiver.dollar += amount
            self._record_dollar_transaction(
                session,
                sender,
                -amount,
                "transfer_out",
                note="Userga dollar o'tkazma",
                counterparty=receiver,
            )
            self._record_dollar_transaction(
                session,
                receiver,
                amount,
                "transfer_in",
                note="Userdan dollar qabul qilindi",
                counterparty=sender,
            )
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

    async def get_admin_group_id(self) -> int:
        async with self.session_factory() as session:
            raw = await self._get_bot_setting_value(session, ADMIN_GROUP_ID_KEY, "")
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    async def set_admin_group(self, bot: Bot, raw_chat_id: Union[int, str]) -> tuple[bool, str]:
        try:
            chat_id = int(str(raw_chat_id).strip())
        except (TypeError, ValueError):
            return False, "Guruh ID faqat son bo'lishi kerak. Masalan: <code>-1001234567890</code>"
        if chat_id >= 0:
            return False, "Guruh ID manfiy bo'lishi kerak. Masalan: <code>-1001234567890</code>"
        try:
            chat = await bot.get_chat(chat_id)
            await bot.send_message(chat_id, "✅ Admin guruh ulandi. Almaz loglari shu yerga avtomatik yuboriladi.")
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            return False, f"Bot bu guruhni topa olmadi yoki xabar yubora olmaydi: {escape(str(exc))}"
        async with self.session_factory() as session:
            await self._set_bot_setting_value(session, ADMIN_GROUP_ID_KEY, str(chat_id))
            latest_tx_id = await session.scalar(select(func.max(DiamondTransaction.id)))
            await self._set_bot_setting_value(session, DIAMOND_LOG_LAST_SENT_ID_KEY, str(int(latest_tx_id or 0)))
            await session.commit()
        title = escape(getattr(chat, "title", None) or str(chat_id))
        return True, f"✅ Admin guruh ulandi: <b>{title}</b>\nID: <code>{chat_id}</code>"

    async def clear_admin_group(self) -> str:
        async with self.session_factory() as session:
            await self._set_bot_setting_value(session, ADMIN_GROUP_ID_KEY, "")
            await session.commit()
        return "✅ Admin guruh o'chirildi. Almaz loglari avtomatik yuborilmaydi."

    async def welcome_settings(self, chat_id: int) -> dict[str, str]:
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                group = Group(chat_id=chat_id, title="Group")
                session.add(group)
                await session.commit()
            return {
                "enabled": "1" if group.welcome_enabled is not False else "0",
                "text": group.welcome_text or WELCOME_DEFAULT_TEXT,
                "media_type": group.welcome_media_type or "",
                "media_file_id": group.welcome_media_file_id or "",
            }

    async def welcome_settings_text(self, chat_id: int) -> str:
        settings = await self.welcome_settings(chat_id)
        enabled = settings["enabled"] == "1"
        status = "🟢 yoqilgan" if enabled else "🔴 o'chirilgan"
        media_type = settings["media_type"] or "yo'q"
        text = escape(settings["text"])
        return (
            "👋 <b>Guruh salomlashuvi</b>\n\n"
            f"Holat: {status}\n"
            f"Media: <b>{escape(media_type)}</b>\n\n"
            "Xabar doim user metkasi bilan boshlanadi. Admin kiritgan matn metkadan keyin chiqadi.\n\n"
            f"Joriy matn:\n<code>{text}</code>"
        )

    async def toggle_welcome_enabled(self, chat_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                group = Group(chat_id=chat_id, title="Group")
                session.add(group)
            group.welcome_enabled = group.welcome_enabled is False
            enabled = group.welcome_enabled is not False
            await session.commit()
        return enabled, "✅ Salomlashuv yoqildi." if enabled else "✅ Salomlashuv o'chirildi."

    async def set_welcome_text(self, chat_id: int, text: str) -> tuple[bool, str]:
        value = " ".join((text or "").strip().split())
        if not 1 <= len(value) <= 900:
            return False, "Matn 1 dan 900 belgigacha bo'lishi kerak."
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                group = Group(chat_id=chat_id, title="Group")
                session.add(group)
            group.welcome_text = value
            await session.commit()
        return True, "✅ Salomlashuv matni yangilandi."

    async def set_welcome_media(self, chat_id: int, media_type: str, file_id: str) -> tuple[bool, str]:
        if media_type not in {"photo", "video", "animation", "document"} or not file_id:
            return False, "Media noto'g'ri. Photo, video, gif yoki document yuboring."
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                group = Group(chat_id=chat_id, title="Group")
                session.add(group)
            group.welcome_media_type = media_type
            group.welcome_media_file_id = file_id
            await session.commit()
        return True, "✅ Salomlashuv mediasi yangilandi."

    async def clear_welcome_media(self, chat_id: int) -> str:
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                group = Group(chat_id=chat_id, title="Group")
                session.add(group)
            group.welcome_media_type = ""
            group.welcome_media_file_id = ""
            await session.commit()
        return "✅ Salomlashuv mediasi o'chirildi."

    async def send_welcome_message(self, bot: Bot, chat_id: int, tg_user: TgUser) -> None:
        settings = await self.welcome_settings(chat_id)
        if settings["enabled"] != "1":
            return
        mention = self._tg_mention(tg_user.id, tg_user.full_name)
        text = escape(settings["text"] or WELCOME_DEFAULT_TEXT)
        caption = f"{mention} {text}".strip()
        media_type = settings["media_type"]
        media_file_id = settings["media_file_id"]
        try:
            if media_type == "photo" and media_file_id:
                await bot.send_photo(chat_id, media_file_id, caption=caption)
            elif media_type == "video" and media_file_id:
                await bot.send_video(chat_id, media_file_id, caption=caption)
            elif media_type == "animation" and media_file_id:
                await bot.send_animation(chat_id, media_file_id, caption=caption)
            elif media_type == "document" and media_file_id:
                await bot.send_document(chat_id, media_file_id, caption=caption)
            else:
                await bot.send_message(chat_id, caption)
        except TelegramBadRequest:
            try:
                await bot.send_message(chat_id, caption)
            except TelegramForbiddenError:
                return
        except TelegramForbiddenError:
            return

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
            self._record_diamond_transaction(
                session,
                user,
                -amount,
                "diamond_to_dollar",
                note=f"Almaz dollarga almashtirildi: {dollars} dollar",
            )
            await session.commit()

        return True, f"✅ 💎 {amount} almaz → 💵 {dollars} dollar almashtirildi."

    async def buy_shop_item(self, telegram_id: int, item_key: str) -> tuple[bool, str]:
        prices: dict[str, tuple[int, str, str, Union[int, str]]] = {
            "protection": (100, "dollar", "protection", 1),
            "vote_protection": (1, "diamonds", "vote_protection", 1),
            "drug_protection": (100, "dollar", "drug_protection", 1),
            "mask": (100, "dollar", "mask", 1),
            "killer_protection": (2, "diamonds", "killer_protection", 1),
            "miner_protection": (300, "dollar", "miner_protection", 1),
        }
        if item_key.startswith("role:"):
            role_value = item_key.split(":", maxsplit=1)[1]
            shop_role = SHOP_ROLE_BY_VALUE.get(role_value)
            if shop_role is None:
                return False, "Bunday rol do'konda topilmadi."
            async with self.session_factory() as session:
                user = (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
                if user is None:
                    return False, "Avval /start bosing."
                if user.next_game_role:
                    return False, f"Keyingi o'yin uchun rol allaqachon tanlangan: {role_label(user.next_game_role)}"
                balance = user.diamonds if shop_role.currency == "diamonds" else user.dollar
                icon = "💎" if shop_role.currency == "diamonds" else "💵"
                if balance < shop_role.price:
                    return False, f"Balans yetarli emas. Kerak: {icon} {shop_role.price}"
                if shop_role.currency == "diamonds":
                    user.diamonds -= shop_role.price
                    self._record_diamond_transaction(
                        session,
                        user,
                        -shop_role.price,
                        "shop_role_buy",
                        note=f"Keyingi o'yin roli: {role_label(shop_role.role)}",
                    )
                else:
                    user.dollar -= shop_role.price
                user.next_game_role = shop_role.role.value
                await session.commit()
            return True, f"✅ Keyingi o'yinda sizga {role_label(shop_role.role)} roli beriladi."

        if item_key.startswith("disable_role:"):
            role_value = item_key.split(":", maxsplit=1)[1]
            try:
                disabled_role = Role(role_value)
            except ValueError:
                return False, "Bunday faol rol topilmadi."
            if disabled_role not in ACTIVE_ROLE_POOL:
                return False, "Bu rolni faol role pool'dan o'chirib bo'lmaydi."
            async with self.session_factory() as session:
                user = (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
                if user is None:
                    return False, "Avval /start bosing."
                if user.next_game_disabled_role:
                    return False, "Keyingi o'yin uchun faol rol allaqachon o'chirilgan."
                if user.dollar < 100:
                    return False, "Balans yetarli emas. Kerak: 💵 100"
                user.dollar -= 100
                user.next_game_disabled_role = disabled_role.value
                await session.commit()
            return True, f"✅ Keyingi o'yinda {role_label(disabled_role)} pool'dan olib tashlanadi."

        item = prices.get(item_key)
        if item is None:
            return False, "Bunday mahsulot topilmadi."
        price, currency, field_name, value = item
        async with self.session_factory() as session:
            user = (await session.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
            if user is None:
                return False, "Avval /start bosing."
            balance = user.diamonds if currency == "diamonds" else user.dollar
            icon = "💎" if currency == "diamonds" else "💵"
            if balance < price:
                return False, f"Balans yetarli emas. Kerak: {icon} {price}"
            if currency == "diamonds":
                user.diamonds -= price
                self._record_diamond_transaction(
                    session,
                    user,
                    -price,
                    "shop_item_buy",
                    note=f"Do'kon mahsuloti: {item_key}",
                )
            else:
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

    @staticmethod
    def _diamond_action_label(action: str) -> str:
        labels = {
            "admin_grant": "admin kredit",
            "admin_bust": "admin bankrot",
            "diamond_payment": "Stars xarid",
            "diamond_to_dollar": "dollarga almashtirish",
            "game_participation_reward": "o'yin ishtirok mukofoti",
            "game_winner_reward": "g'olib mukofoti",
            "giveaway_create": "sovg'a ochish",
            "giveaway_refund": "sovg'a qaytarish",
            "giveaway_win": "sovg'a yutish",
            "hero_add_points": "geroy ball",
            "hero_buy": "geroy xarid",
            "hero_market_buy": "geroy marketplace xarid",
            "hero_market_sale": "geroy marketplace sotuv",
            "hero_sale_cancel": "geroy sotuvdan qaytarish",
            "hojiaka_grant": "hojiaka ehson",
            "miner_reward": "konchi topilmasi",
            "mashka_steal_in": "mashka o'g'irlik kirim",
            "mashka_steal_out": "mashka o'g'irlik chiqim",
            "premium_group_contribution": "premium guruh",
            "shop_item_buy": "do'kon mahsulot",
            "shop_role_buy": "rol xarid",
            "transfer_in": "o'tkazma kirim",
            "transfer_out": "o'tkazma chiqim",
        }
        return labels.get(action, action.replace("_", " "))

    @staticmethod
    def _split_report_lines(lines: list[str], max_chars: int = 3600) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for line in lines:
            line_len = len(line) + 1
            if current and current_len + line_len > max_chars:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            current.append(line)
            current_len += line_len
        if current:
            chunks.append("\n".join(current))
        return chunks

    @staticmethod
    def _format_tx_time(value: Optional[datetime]) -> str:
        if value is None:
            return "--"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _short_text(value: Optional[str], limit: int = 140) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[: max(0, limit - 1)]}…"

    @staticmethod
    def _diamond_log_threshold_filter():
        return func.abs(DiamondTransaction.amount) >= DIAMOND_LOG_MIN_AMOUNT

    async def _owner_diamond_audit_lines(self, limit: int = 15, *, title: str = "💎 <b>Almaz loglari</b>") -> list[str]:
        limit = min(max(5, int(limit)), 50)
        threshold_filter = self._diamond_log_threshold_filter()
        async with self.session_factory() as session:
            income_expr = func.coalesce(
                func.sum(case((DiamondTransaction.amount > 0, DiamondTransaction.amount), else_=0)),
                0,
            )
            expense_expr = func.coalesce(
                func.sum(case((DiamondTransaction.amount < 0, -DiamondTransaction.amount), else_=0)),
                0,
            )
            total_income, total_expense, tx_count = (
                await session.execute(
                    select(income_expr, expense_expr, func.count(DiamondTransaction.id)).where(threshold_filter)
                )
            ).one()
            by_action = (
                await session.execute(
                    select(
                        DiamondTransaction.action,
                        func.count(DiamondTransaction.id),
                        func.coalesce(func.sum(DiamondTransaction.amount), 0),
                        func.coalesce(
                            func.sum(case((DiamondTransaction.amount > 0, DiamondTransaction.amount), else_=0)),
                            0,
                        ),
                        func.coalesce(
                            func.sum(case((DiamondTransaction.amount < 0, -DiamondTransaction.amount), else_=0)),
                            0,
                        ),
                    )
                    .where(threshold_filter)
                    .group_by(DiamondTransaction.action)
                    .order_by(func.count(DiamondTransaction.id).desc())
                    .limit(10)
                )
            ).all()
            user_income_expr = func.coalesce(
                func.sum(case((DiamondTransaction.amount > 0, DiamondTransaction.amount), else_=0)),
                0,
            ).label("income")
            user_expense_expr = func.coalesce(
                func.sum(case((DiamondTransaction.amount < 0, -DiamondTransaction.amount), else_=0)),
                0,
            ).label("expense")
            top_users = (
                await session.execute(
                    select(
                        DiamondTransaction.user_telegram_id,
                        func.max(DiamondTransaction.user_name),
                        user_income_expr,
                        user_expense_expr,
                    )
                    .where(threshold_filter)
                    .group_by(DiamondTransaction.user_telegram_id)
                    .order_by(user_expense_expr.desc(), user_income_expr.desc())
                    .limit(10)
                )
            ).all()
            recent = (
                await session.execute(
                    select(DiamondTransaction)
                    .where(threshold_filter)
                    .order_by(DiamondTransaction.created_at.desc(), DiamondTransaction.id.desc())
                    .limit(limit)
                )
            ).scalars().all()

        if not tx_count:
            return [
                title,
                "",
                f"Hali <b>{DIAMOND_LOG_MIN_AMOUNT}</b> almaz yoki undan yuqori kirim-chiqim logi yozilmagan.\n"
                "Mayda amallar bu bo'limda ko'rsatilmaydi.",
            ]

        lines = [
            title,
            "",
            f"Filter: <b>{DIAMOND_LOG_MIN_AMOUNT}</b> almaz va undan yuqori",
            f"📥 Jami kirim: <b>{int(total_income or 0)}</b>",
            f"📤 Jami sarf: <b>{int(total_expense or 0)}</b>",
            f"🧾 Amallar soni: <b>{int(tx_count or 0)}</b>",
            "",
            "📌 <b>Nimalarga sarflanmoqda / olinmoqda:</b>",
        ]
        for action, count, net, income, expense in by_action:
            label = self._diamond_action_label(str(action))
            lines.append(
                f"• {escape(label)}: kirim <b>{int(income or 0)}</b>, "
                f"sarf <b>{int(expense or 0)}</b>, net <b>{int(net or 0)}</b> ({int(count or 0)} ta)"
            )

        lines.extend(["", "👥 <b>TOP userlar:</b>"])
        for index, (telegram_id, name, income, expense) in enumerate(top_users, start=1):
            mention = self._tg_mention(int(telegram_id), str(name or telegram_id))
            lines.append(f"{index}. {mention}: kirim <b>{int(income or 0)}</b>, sarf <b>{int(expense or 0)}</b>")

        lines.extend(["", f"🕘 <b>Oxirgi {limit} amal:</b>"])
        for item in recent:
            sign = "+" if int(item.amount or 0) > 0 else ""
            label = self._diamond_action_label(item.action)
            when = self._format_tx_time(item.created_at)
            note = f"\n   📝 {escape(self._short_text(item.note))}" if item.note else ""
            counterparty = ""
            if item.counterparty_telegram_id:
                counterparty_name = escape(self._short_text(item.counterparty_name or str(item.counterparty_telegram_id), 64))
                counterparty = f"\n   ↔️ {counterparty_name} (<code>{item.counterparty_telegram_id}</code>)"
            chat = f"\n   🏠 chat: <code>{item.chat_id}</code>" if item.chat_id else ""
            user_link = self._tg_mention(item.user_telegram_id, item.user_name)
            lines.append(
                f"{when} • {user_link}: <b>{sign}{int(item.amount or 0)}</b> "
                f"({escape(label)}) → balans <b>{int(item.balance_after or 0)}</b>{counterparty}{chat}{note}"
            )
        return lines

    async def owner_diamond_audit_text(self, limit: int = 15) -> str:
        group_id = await self.get_admin_group_id()
        group_text = f"<code>{group_id}</code>" if group_id else "<b>ulanmagan</b>"
        lines = await self._owner_diamond_audit_lines(limit)
        if len(lines) >= 2:
            lines.insert(2, f"🏠 Log guruhi: {group_text}")
        return "\n".join(lines)

    async def owner_diamond_audit_chunks(self, limit: int = 30) -> list[str]:
        lines = await self._owner_diamond_audit_lines(limit, title="💎 <b>Almaz loglari hisoboti</b>")
        chunks = self._split_report_lines(lines)
        if len(chunks) <= 1:
            return chunks
        total = len(chunks)
        return [f"{chunk}\n\n<b>Qism:</b> {index}/{total}" for index, chunk in enumerate(chunks, start=1)]

    async def send_owner_diamond_audit(self, bot: Bot, chat_id: int, limit: int = 30) -> tuple[bool, str, int]:
        if chat_id == 0:
            return False, "Admin guruh sozlanmagan. Admin paneldan <b>Admin guruh</b> bo'limida ulang.", 0
        if chat_id > 0:
            return False, "Admin guruh ID guruh/superguruh ID bo'lishi kerak. Odatda u manfiy son bo'ladi.", 0

        chunks = await self.owner_diamond_audit_chunks(limit)
        sent = 0
        try:
            chat = await bot.get_chat(chat_id)
            chat_title = getattr(chat, "title", None) or str(chat_id)
            for chunk in chunks:
                await bot.send_message(chat_id=chat_id, text=chunk)
                sent += 1
                await asyncio.sleep(0.05)
        except TelegramForbiddenError:
            return False, "Bot admin guruhga kira olmayapti yoki xabar yuborishga ruxsati yo'q.", sent
        except TelegramBadRequest as exc:
            return False, f"Telegram xatosi: {escape(str(exc))}", sent
        return True, f"Almaz loglari <b>{escape(chat_title)}</b> guruhiga yuborildi. Xabarlar: <b>{sent}</b> ta.", sent

    def _diamond_transaction_line(self, item: DiamondTransaction) -> str:
        sign = "+" if int(item.amount or 0) > 0 else ""
        label = self._diamond_action_label(item.action)
        when = self._format_tx_time(item.created_at)
        user_link = self._tg_mention(item.user_telegram_id, item.user_name)
        parts = [
            f"#{item.id} • {when}",
            f"{user_link}: <b>{sign}{int(item.amount or 0)}</b> ({escape(label)})",
            f"Balans: <b>{int(item.balance_after or 0)}</b>",
        ]
        if item.counterparty_telegram_id:
            counterparty = escape(self._short_text(item.counterparty_name or str(item.counterparty_telegram_id), 64))
            parts.append(f"↔️ {counterparty} (<code>{item.counterparty_telegram_id}</code>)")
        if item.chat_id:
            parts.append(f"🏠 <code>{item.chat_id}</code>")
        if item.note:
            parts.append(f"📝 {escape(self._short_text(item.note, 120))}")
        return "\n".join(parts)

    async def send_pending_diamond_logs(self, bot: Bot) -> int:
        chat_id = await self.get_admin_group_id()
        if chat_id == 0:
            return 0
        if chat_id > 0:
            logger.warning("Admin group id must be a group/supergroup id, got %s", chat_id)
            return 0

        async with self.session_factory() as session:
            raw_last_id = await self._get_bot_setting_value(session, DIAMOND_LOG_LAST_SENT_ID_KEY, "0")
            try:
                last_id = max(0, int(raw_last_id))
            except (TypeError, ValueError):
                last_id = 0
            rows = (
                await session.execute(
                    select(DiamondTransaction)
                    .where(DiamondTransaction.id > last_id)
                    .order_by(DiamondTransaction.id.asc())
                    .limit(200)
                )
            ).scalars().all()

        if not rows:
            return 0

        newest_id = max(int(item.id) for item in rows)
        visible_rows = [item for item in rows if abs(int(item.amount or 0)) >= DIAMOND_LOG_MIN_AMOUNT]
        if not visible_rows:
            async with self.session_factory() as session:
                await self._set_bot_setting_value(session, DIAMOND_LOG_LAST_SENT_ID_KEY, str(newest_id))
                await session.commit()
            return 0

        lines = ["💎 <b>Yangi almaz loglari</b>", ""]
        lines.append(f"Filter: <b>{DIAMOND_LOG_MIN_AMOUNT}</b> almaz va undan yuqori")
        lines.append("")
        for item in visible_rows:
            lines.extend([self._diamond_transaction_line(item), ""])
        chunks = self._split_report_lines(lines)

        sent = 0
        try:
            for chunk in chunks:
                await bot.send_message(chat_id=chat_id, text=chunk)
                sent += 1
                await asyncio.sleep(0.05)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Failed to send pending diamond logs to %s: %s", chat_id, exc)
            return sent

        async with self.session_factory() as session:
            await self._set_bot_setting_value(session, DIAMOND_LOG_LAST_SENT_ID_KEY, str(newest_id))
            await session.commit()
        return sent

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
            self._record_diamond_transaction(
                session,
                user,
                diamonds,
                "admin_grant",
                note=f"Admin kredit: dollar={dollar}, almaz={diamonds}",
            )
            await session.commit()
        return True, f"✅ Berildi: <tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji> {dollar}, <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {diamonds}"

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
                "Hozircha guruhlar <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> almaz yubormagan.\n"
                "Guruhda <code>/gsend miqdor</code> yozib reytingga chiqish mumkin."
            )
        return "🎲 <b>Premium guruhlar</b>\n\nKerakli guruhni tanlang:"

    async def _premium_reset_interval_minutes_in_session(self, session: AsyncSession) -> int:
        raw = await self._get_bot_setting_value(session, PREMIUM_RESET_INTERVAL_MINUTES_KEY, "0")
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

    async def get_premium_reset_interval_minutes(self) -> int:
        async with self.session_factory() as session:
            return await self._premium_reset_interval_minutes_in_session(session)

    async def premium_reset_timer_text(self) -> str:
        minutes = await self.get_premium_reset_interval_minutes()
        if minutes <= 0:
            return "⏱ Premium timer: <b>o'chirilgan</b>"
        async with self.session_factory() as session:
            next_reset = await session.scalar(
                select(func.min(PremiumGroup.reset_at)).where(
                    PremiumGroup.is_active.is_(True),
                    PremiumGroup.total_diamonds > 0,
                    PremiumGroup.reset_at.is_not(None),
                )
            )
        if next_reset:
            remaining = max(0, int((self._ensure_utc(next_reset) - self._now_utc()).total_seconds() // 60))
            return (
                f"⏱ Premium timer: <b>{self._format_minutes(minutes)}</b>\n"
                f"⏳ Keyingi bankrot: taxminan <b>{self._format_minutes(remaining)}</b>"
            )
        return f"⏱ Premium timer: <b>{self._format_minutes(minutes)}</b>"

    async def set_premium_reset_interval_minutes(self, raw_minutes: Union[int, str]) -> tuple[bool, str]:
        try:
            minutes = int(str(raw_minutes).strip())
        except (TypeError, ValueError):
            return False, "Timer faqat son bo'lishi kerak. Masalan: <code>1440</code>"
        if minutes < 0:
            return False, "Timer manfiy bo'lmaydi. O'chirish uchun <code>0</code> yuboring."
        if minutes > 525600:
            return False, "Timer juda katta. Eng ko'pi: <code>525600</code> daqiqa (1 yil)."

        async with self.session_factory() as session:
            await self._set_bot_setting_value(session, PREMIUM_RESET_INTERVAL_MINUTES_KEY, str(minutes))
            active_groups = (
                await session.execute(
                    select(PremiumGroup).where(
                        PremiumGroup.is_active.is_(True),
                        PremiumGroup.total_diamonds > 0,
                    )
                )
            ).scalars().all()
            reset_at = self._now_utc() + timedelta(minutes=minutes) if minutes > 0 else None
            for group in active_groups:
                group.reset_at = reset_at
            await session.commit()

        if minutes == 0:
            return True, "✅ Premium timer o'chirildi. Guruhlar avtomatik bankrot qilinmaydi."
        return (
            True,
            f"✅ Premium timer yangilandi: <b>{self._format_minutes(minutes)}</b>.\n"
            "Aktiv premium guruhlar uchun vaqt hozirdan qayta hisoblandi.",
        )

    async def _clear_premium_group_balance(self, session: AsyncSession, group: PremiumGroup) -> None:
        group.total_diamonds = 0
        group.diamond_price = 0
        group.top_sender_telegram_id = None
        group.top_sender_name = None
        group.top_sender_diamonds = 0
        group.reset_at = None
        group.is_active = False
        contributions = (
            await session.execute(
                select(PremiumGroupContribution).where(PremiumGroupContribution.premium_group_id == group.id)
            )
        ).scalars().all()
        for contribution in contributions:
            await session.delete(contribution)

    async def reset_expired_premium_groups(self) -> int:
        async with self.session_factory() as session:
            interval_minutes = await self._premium_reset_interval_minutes_in_session(session)
            if interval_minutes <= 0:
                return 0
            now = self._now_utc()
            expired_groups = (
                await session.execute(
                    select(PremiumGroup).where(
                        PremiumGroup.is_active.is_(True),
                        PremiumGroup.total_diamonds > 0,
                        PremiumGroup.reset_at.is_not(None),
                        PremiumGroup.reset_at <= now,
                    )
                )
            ).scalars().all()
            for group in expired_groups:
                await self._clear_premium_group_balance(session, group)
            await session.commit()
        return len(expired_groups)

    async def premium_reset_watchdog(self) -> None:
        reset_count = await self.reset_expired_premium_groups()
        if reset_count:
            logger.info("Premium group timer reset %s group(s)", reset_count)

    async def owner_premium_groups_manage_text(self) -> str:
        groups = await self.premium_groups(include_inactive=True)
        timer_text = await self.premium_reset_timer_text()
        if not groups:
            return f"🎲 <b>Premium guruhlar boshqaruvi</b>\n\n{timer_text}\n\nHozircha ro'yxatda guruh yo'q."
        lines = [
            "🎲 <b>Premium guruhlar boshqaruvi</b>",
            "",
            timer_text,
            "",
            "Bankrot qilish uchun guruh tugmasini bosing:",
            "",
        ]
        for group in groups:
            status = "aktiv" if group.is_active and (group.total_diamonds or 0) > 0 else "bankrot"
            if status == "aktiv" and group.reset_at:
                remaining = max(0, int((self._ensure_utc(group.reset_at) - self._now_utc()).total_seconds() // 60))
                status = f"{status}, {self._format_minutes(remaining)} qoldi"
            lines.append(f"<b>{group.title}</b> | <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {group.total_diamonds or 0} | {status}")
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
            group.reset_at = None
            group.is_active = False
            await self._clear_premium_group_balance(session, group)
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
                return False, f"Balans yetarli emas. Kerak: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {diamonds}"

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
            self._record_diamond_transaction(
                session,
                fresh_user,
                -diamonds,
                "premium_group_contribution",
                note=f"Premium guruh reytingi: {chat_title or chat_id}",
                chat_id=chat_id,
            )
            group.total_diamonds = int(group.total_diamonds or 0) + diamonds
            group.diamond_price = group.total_diamonds
            reset_interval_minutes = await self._premium_reset_interval_minutes_in_session(session)
            group.reset_at = (
                self._now_utc() + timedelta(minutes=reset_interval_minutes)
                if reset_interval_minutes > 0
                else None
            )
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
            f"✅ {self._tg_mention(tg_user.id, user.display_name)} guruh reytingi uchun <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {diamonds} almaz yubordi.\n"
            f"🎲 Guruh jami: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {total}\n"
            f"👤 Siz yuborgan jami: <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {user_total}",
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
                f"<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> Kirish narxi: <b>{group.diamond_price}</b>\n"
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

    async def group_timeout(self, chat_id: int, field: str) -> int:
        time_key_map = {
            "registration_timeout": "registration_time",
            "night_timeout": "night_time",
            "day_discussion_timeout": "day_time",
            "day_voting_timeout": "vote_time",
        }
        time_key = time_key_map.get(field)
        if time_key:
            gsm = GroupSettingsManager(self.session_factory)
            seconds = await gsm.get_time_setting(chat_id, time_key)
            if seconds > 0:
                return seconds
        defaults = {
            "registration_timeout": self.settings.registration_timeout,
            "night_timeout": self.settings.night_timeout,
            "day_discussion_timeout": self.settings.day_discussion_timeout,
            "day_voting_timeout": self.settings.day_voting_timeout,
        }
        default = defaults.get(field, 60)
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                return default
            return max(10, int(getattr(group, field, None) or default))

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

    async def update_group_setting(self, chat_id: int, field: str, value: object) -> tuple[bool, str]:
        """Guruh sozlamalarini yangilash. Aktiv oyni davomida mode o'zgartirish mumkin emas."""
        async with self.session_factory() as session:
            group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
            if group is None:
                group = Group(chat_id=chat_id, title="Group")
                session.add(group)
            
            if field == "registration_timeout":
                group.registration_timeout = max(10, int(value))
                await session.commit()
                return True, f"✅ Registration timeout: {group.registration_timeout} soniya"
            elif field == "night_timeout":
                group.night_timeout = max(10, int(value))
                await session.commit()
                return True, f"✅ Tun vaqti: {group.night_timeout} soniya"
            elif field == "day_discussion_timeout":
                group.day_discussion_timeout = max(10, int(value))
                await session.commit()
                return True, f"✅ Kun muhokamasi: {group.day_discussion_timeout} soniya"
            elif field == "day_voting_timeout":
                group.day_voting_timeout = max(10, int(value))
                await session.commit()
                return True, f"✅ Ovoz berish vaqti: {group.day_voting_timeout} soniya"
            elif field == "min_players":
                group.min_players = max(4, min(int(value), 30))
                await session.commit()
                return True, f"✅ Minimal o'yinchilar: {group.min_players}"
            elif field == "role_preset":
                preset = str(value)
                if preset not in GAME_MODES and preset not in {"black23", "extended35"}:
                    return False, "❌ Noma'lum role preset"
                
                # Registration vaqtida mode almashtirish mumkin, aktiv o'yinda esa mumkin emas.
                active_game = await self.find_active_game(session, chat_id)
                if active_game is not None and active_game.status == GameStatus.ACTIVE.value:
                    current_preset = active_game.role_preset or "black23"
                    if current_preset != preset:
                        current_name = role_preset_label(current_preset)
                        return False, f"❌ Aktiv oyin davomida mode o'zgartira olmaysiz!\nJoriy mode: <b>{current_name}</b>"
                
                group.role_preset = preset
                if active_game is not None and active_game.status == GameStatus.REGISTRATION.value:
                    active_game.role_preset = preset
                await session.commit()
                return True, f"✅ Role preset: {role_preset_label(preset)}"
            
            return False, "❌ Noma'lum sozlama"

    def format_role_preset_settings(self, group: Group) -> str:
        preset = group.role_preset or "black23"
        return (
            "🎭 <b>Role settings</b>\n\n"
            f"Joriy preset: <b>{role_preset_label(preset)}</b>\n"
            f"Maksimal tavsiya qilingan o'yinchi: <b>{role_preset_max_players(preset)}</b>\n\n"
            "<b>Classic</b> - eski klassik taqsimotni saqlaydi.\n"
            "<b>Super</b> - faol rollarni minimal o'yinchi soniga qarab ertaroq beradi, yetmasa Tinch aholi bilan to'ldiradi.\n"
            "<b>Mega</b> - faqat faol rollar, Tinch aholi hech qachon tushmaydi."
        )
