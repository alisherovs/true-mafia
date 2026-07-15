"""VIP badge / nickname display helpers.

Display-only layer: never used for game logic (votes, roles, win conditions).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from typing import Any, Optional

DEFAULT_VIP_BADGE = "👑"
BADGE_POSITION_BEFORE = "before"
BADGE_POSITION_AFTER = "after"
VIP_NICK_MAX_LEN = 24
VIP_NICK_MIN_LEN = 2

# Safe preset badges (unicode). Avoid role/team/UI confusable symbols.
VIP_BADGE_PRESETS: tuple[str, ...] = (
    "👑",
    "💎",
    "⭐",
    "🔥",
    "🌙",
    "⚡",
    "💫",
    "🎩",
    "🦊",
    "🐉",
    "🦁",
    "🦈",
    "🦋",
    "🌹",
    "🎯",
    "🏆",
)

# Symbols that clash with roles / voting / tournament UI.
_BADGE_BLACKLIST: frozenset[str] = frozenset(
    {
        "🤵🏻",
        " indoors",
        "🔪",
        "🔫",
        "🕵",
        "🕵🏼",
        "🛡",
        "⚖",
        "⚖️",
        "💀",
        "☠",
        "☠️",
        "✅",
        "❌",
        "🔵",
        "🔴",
        "1️⃣",
        "2️⃣",
        "3️⃣",
        "4️⃣",
        "5️⃣",
        "6️⃣",
        "7️⃣",
        "8️⃣",
        "9️⃣",
        "🔟",
        "💊",
        "🃏",
        "👷",
        "🧟",
        "🥷",
    }
)

_INVISIBLE = {
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

_NICK_FORBIDDEN_RE = re.compile(r"[\n\r\t<>@#]")


@dataclass(frozen=True)
class VipStyle:
    """Snapshot of VIP cosmetic style for display."""

    active: bool
    badge: str = DEFAULT_VIP_BADGE
    badge_emoji_id: Optional[str] = None
    position: str = BADGE_POSITION_BEFORE
    hidden: bool = False
    nickname: Optional[str] = None

    @property
    def show_badge(self) -> bool:
        return self.active and not self.hidden


def is_vip_active(vip_until: Optional[datetime], now: Optional[datetime] = None) -> bool:
    if vip_until is None:
        return False
    now = now or datetime.now(timezone.utc)
    if vip_until.tzinfo is None:
        vip_until = vip_until.replace(tzinfo=timezone.utc)
    else:
        vip_until = vip_until.astimezone(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return vip_until > now


def vip_days_left(vip_until: Optional[datetime], now: Optional[datetime] = None) -> int:
    if not is_vip_active(vip_until, now):
        return 0
    now = now or datetime.now(timezone.utc)
    if vip_until is None:
        return 0
    if vip_until.tzinfo is None:
        vip_until = vip_until.replace(tzinfo=timezone.utc)
    else:
        vip_until = vip_until.astimezone(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0, (vip_until - now).days)


def style_from_user(user: Any, now: Optional[datetime] = None) -> VipStyle:
    active = is_vip_active(getattr(user, "vip_until", None), now)
    position = getattr(user, "vip_badge_position", None) or BADGE_POSITION_BEFORE
    if position not in {BADGE_POSITION_BEFORE, BADGE_POSITION_AFTER}:
        position = BADGE_POSITION_BEFORE
    badge = (getattr(user, "vip_badge", None) or DEFAULT_VIP_BADGE).strip() or DEFAULT_VIP_BADGE
    emoji_id = getattr(user, "vip_badge_emoji_id", None) or None
    if emoji_id:
        emoji_id = str(emoji_id).strip() or None
    nick = getattr(user, "vip_nickname", None)
    if nick:
        nick = str(nick).strip() or None
    hidden = bool(getattr(user, "vip_badge_hidden", False))
    return VipStyle(
        active=active,
        badge=badge,
        badge_emoji_id=emoji_id,
        position=position,
        hidden=hidden,
        nickname=nick,
    )


def style_from_player(player: Any) -> VipStyle:
    """Read snapshot fields from GamePlayer (may be inactive if no badge stored)."""
    badge = getattr(player, "vip_badge", None)
    emoji_id = getattr(player, "vip_badge_emoji_id", None)
    position = getattr(player, "vip_badge_position", None) or BADGE_POSITION_BEFORE
    if position not in {BADGE_POSITION_BEFORE, BADGE_POSITION_AFTER}:
        position = BADGE_POSITION_BEFORE
    show = bool(getattr(player, "vip_show_badge", False))
    if not show and not badge and not emoji_id:
        return VipStyle(active=False)
    return VipStyle(
        active=show,
        badge=(badge or DEFAULT_VIP_BADGE),
        badge_emoji_id=str(emoji_id).strip() if emoji_id else None,
        position=position,
        hidden=not show,
        nickname=None,
    )


def resolve_game_name(user: Any, fallback: str) -> str:
    """VIP nickname for in-game display; never affects identity IDs."""
    style = style_from_user(user)
    if style.active and style.nickname:
        return style.nickname[:255]
    base = (getattr(user, "display_name", None) or fallback or "Player").strip()
    return base[:255] if base else "Player"


def snapshot_fields(user: Any, fallback_name: str) -> dict[str, Any]:
    """Fields to copy onto GamePlayer at join (cosmetic only)."""
    style = style_from_user(user)
    name = resolve_game_name(user, fallback_name)
    if not style.active or style.hidden:
        return {
            "display_name": name,
            "vip_badge": None,
            "vip_badge_emoji_id": None,
            "vip_badge_position": BADGE_POSITION_BEFORE,
            "vip_show_badge": False,
        }
    return {
        "display_name": name,
        "vip_badge": style.badge[:32],
        "vip_badge_emoji_id": style.badge_emoji_id,
        "vip_badge_position": style.position,
        "vip_show_badge": True,
    }


def badge_html(style: VipStyle) -> str:
    if not style.show_badge:
        return ""
    fallback = escape(style.badge or DEFAULT_VIP_BADGE)
    if style.badge_emoji_id and str(style.badge_emoji_id).isdigit():
        return f'<tg-emoji emoji-id="{escape(str(style.badge_emoji_id), quote=True)}">{fallback}</tg-emoji>'
    return fallback


def badge_plain(style: VipStyle) -> str:
    """Unicode-only badge for button labels (no custom emoji HTML)."""
    if not style.show_badge:
        return ""
    return (style.badge or DEFAULT_VIP_BADGE).strip()


def compose_labeled_name(name: str, style: VipStyle, *, html: bool) -> str:
    """Join badge + name. When html=True, name is already escaped HTML (or mention)."""
    if not style.show_badge:
        return name
    badge = badge_html(style) if html else badge_plain(style)
    if not badge:
        return name
    if style.position == BADGE_POSITION_AFTER:
        return f"{name} {badge}"
    return f"{badge} {name}"


def format_mention(
    user_id: int,
    display_name: str,
    style: Optional[VipStyle] = None,
) -> str:
    safe = escape(display_name or "Unknown")
    mention = f'<a href="tg://user?id={user_id}">{safe}</a>'
    if style is None:
        return mention
    return compose_labeled_name(mention, style, html=True)


def format_player_mention(player: Any) -> str:
    style = style_from_player(player)
    return format_mention(player.telegram_id, player.display_name or "Unknown", style)


def format_user_mention(user: Any) -> str:
    style = style_from_user(user)
    name = style.nickname if (style.active and style.nickname) else (user.display_name or "Unknown")
    return format_mention(user.telegram_id, name, style)


def format_button_label(display_name: str, style: Optional[VipStyle] = None, max_len: int = 56) -> str:
    name = (display_name or "User").strip() or "User"
    if style is None or not style.show_badge:
        label = name
    else:
        label = compose_labeled_name(name, style, html=False)
    if len(label) > max_len:
        return label[: max_len - 1] + "…"
    return label


def format_player_button(player: Any) -> str:
    return format_button_label(player.display_name or "User", style_from_player(player))


def player_choice_tuple(player: Any) -> tuple[int, str]:
    return player.telegram_id, format_player_button(player)


def sanitize_vip_nickname(raw: str) -> tuple[bool, str]:
    text = (raw or "").strip()
    text = "".join(ch for ch in text if ch not in _INVISIBLE)
    text = re.sub(r"\s+", " ", text)
    if _NICK_FORBIDDEN_RE.search(text):
        return False, "Nikda < > @ # yoki yangi qator bo'lishi mumkin emas."
    if len(text) < VIP_NICK_MIN_LEN:
        return False, f"Nik kamida {VIP_NICK_MIN_LEN} belgidan iborat bo'lsin."
    if len(text) > VIP_NICK_MAX_LEN:
        return False, f"Nik maksimal {VIP_NICK_MAX_LEN} belgi."
    # Must have at least one visible letter/number/symbol
    visible = False
    for ch in text:
        if ch.isspace():
            continue
        cat = unicodedata.category(ch)
        if cat[0] in {"L", "N", "P", "S"}:
            visible = True
            break
    if not visible:
        return False, "Nik ko'rinadigan bo'lishi kerak."
    return True, text


def _is_single_emoji_cluster(text: str) -> bool:
    """Best-effort: one emoji / ZWJ sequence, no plain letters."""
    text = text.strip()
    if not text or len(text) > 16:
        return False
    if any(ch.isalpha() and ord(ch) < 0x300 for ch in text):
        return False
    if any(ch.isdigit() and ord(ch) < 0x300 for ch in text):
        # allow keycap digits only if full cluster — still blacklist numbered keys
        pass
    # Reject pure ascii text
    if text.isascii() and not any(ord(c) > 127 for c in text):
        # ascii-only: only allow if it's not letters (almost never a good badge)
        return False
    return True


def validate_badge_unicode(raw: str) -> tuple[bool, str]:
    text = (raw or "").strip()
    text = "".join(ch for ch in text if ch not in _INVISIBLE)
    if not text:
        return False, "Badge bo'sh bo'lmasin."
    if text in _BADGE_BLACKLIST:
        return False, "Bu belgi o'yin belgilari bilan chalkashadi. Boshqasini tanlang."
    if text in VIP_BADGE_PRESETS:
        return True, text
    if not _is_single_emoji_cluster(text):
        return False, "Faqat bitta emoji yuboring (matn yoki bir nechta emoji emas)."
    # Extra blacklist by contained chars
    for banned in _BADGE_BLACKLIST:
        if banned in text:
            return False, "Bu belgi o'yin belgilari bilan chalkashadi."
    return True, text


def validate_custom_emoji_id(emoji_id: str, fallback: str = DEFAULT_VIP_BADGE) -> tuple[bool, str, str]:
    eid = (emoji_id or "").strip()
    if not eid.isdigit() or len(eid) < 5:
        return False, "", "Noto'g'ri premium emoji ID."
    fb = (fallback or DEFAULT_VIP_BADGE).strip() or DEFAULT_VIP_BADGE
    ok, clean_fb = validate_badge_unicode(fb) if fb not in VIP_BADGE_PRESETS else (True, fb)
    if not ok:
        clean_fb = DEFAULT_VIP_BADGE
    return True, eid, clean_fb


def preview_line(style: VipStyle, sample_name: str = "Ismingiz") -> str:
    name = style.nickname if (style.active and style.nickname) else sample_name
    if not style.active:
        return escape(name)
    safe_name = escape(name)
    return compose_labeled_name(safe_name, style, html=True)
