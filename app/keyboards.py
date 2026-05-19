from __future__ import annotations

from typing import Optional
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import Settings
from app.enums import Role
from app.roles import ACTIVE_ROLE_POOL, SHOP_ROLE_CATALOG, role_label
from app.texts import t

JOKER_CARD_LABELS = {
    1: "♠️",
    2: "♥️",
    3: "♦️",
    4: "♣️",
}

LANGS = [
    ("az", "🇦🇿 Azərbaycanca"),
    ("tr", "🇹🇷 Türkçe"),
    ("en", "🇺🇸 English"),
    ("ru", "🇷🇺 Русский"),
    ("ua", "🇺🇦 Український"),
    ("kz", "🇰🇿 Қазақ"),
    ("uz", "🇺🇿 O'zbek tili"),
    ("id", "🇮🇩 Indonesia"),
]


def _clean_bot_username(username: str) -> str:
    return username.strip().lstrip("@")


def language_keyboard(scope: str = "user", chat_id: Optional[int] = None) -> InlineKeyboardMarkup:
    rows = []
    for code, label in LANGS:
        suffix = f"{scope}:{code}:{chat_id or 0}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"lang:{suffix}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def start_menu_keyboard(
    lang: str,
    settings: Settings,
    is_admin: bool = False,
    news_url: Optional[str] = None,
) -> InlineKeyboardMarkup:
    add_url = f"https://t.me/{_clean_bot_username(settings.bot_username)}?startgroup=true"
    rows = [
        [InlineKeyboardButton(text=t(lang, "add_to_group"), url=add_url)],
        [InlineKeyboardButton(text=t(lang, "premium_groups"), callback_data="premium:info")],
        [
            InlineKeyboardButton(text=t(lang, "lang"), callback_data="lang:menu:user:0"),
            InlineKeyboardButton(text=t(lang, "rules_btn"), callback_data="rules:show"),
        ],
    ]
    if news_url:
        rows.insert(3, [InlineKeyboardButton(text=t(lang, "news"), url=news_url)])
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛡 Admin panel", callback_data="owner:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _toggle_button(icon: str, field: str, user: object | None) -> InlineKeyboardButton:
    enabled = getattr(user, field, True) is not False
    state = "🟢 ON" if enabled else "🔴 OFF"
    return InlineKeyboardButton(text=f"{icon} - {state}", callback_data=f"invtoggle:{field}")


def profile_dashboard_keyboard(
    settings: Settings,
    user: object | None = None,
    is_admin: bool = False,
    news_url: Optional[str] = None,
    has_hero: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            _toggle_button("🛡", "use_protection", user),
            _toggle_button("🧿", "use_killer_protection", user),
        ],
        [
            _toggle_button("⚖️", "use_vote_protection", user),
            _toggle_button("💊", "use_drug_protection", user),
        ],
        [
            _toggle_button("📦", "use_miner_protection", user),
            _toggle_button("🎭", "use_mask", user),
        ],
        [
            _toggle_button("📁", "use_fake_document", user),
        ],
        [InlineKeyboardButton(text="Do'kon", callback_data="shop:open")],
        [
            InlineKeyboardButton(text="💎 Xarid qilish", callback_data="diamond:shop"),
            InlineKeyboardButton(text="💵 Xarid qilish", callback_data="dollar:shop"),
        ],
        [InlineKeyboardButton(text="🎲 Premium guruhlar", callback_data="premium:info")],
        *([[InlineKeyboardButton(text="🥷 Mening geroyim", callback_data="hero:panel")]] if has_hero else []),
    ]
    if news_url:
        rows.append([InlineKeyboardButton(text="Yangiliklar ↗", url=news_url)])
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛡 Admin panel", callback_data="owner:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rules_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Orqaga", callback_data="start:back")],
        ]
    )


ROLE_INFO_ORDER: tuple[Role, ...] = (
    Role.SORCERER,
    Role.SPY,
    Role.WOLF,
    Role.BUM,
    Role.DOCTOR,
    Role.DON,
    Role.MAYOR,
    Role.JESTER,
    Role.WATCHER,
    Role.JOURNALIST,
    Role.MISTRESS,
    Role.COMMISSAR,
    Role.MAFIA,
    Role.MINER,
    Role.PRANKSTER,
    Role.JOKER,
    Role.JUDGE,
    Role.KILLER,
    Role.LUCKY,
    Role.SERGEANT,
    Role.SNITCH,
    Role.ARSONIST,
    Role.CITIZEN,
    Role.LAWYER,
    Role.MAQ,
    Role.HIRED_KILLER,
    Role.CROOK,
    Role.GUARD,
    Role.HOJIAKA,
    Role.MASHKA,
)


def roles_menu_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    buttons = [
        InlineKeyboardButton(text=role_label(role), callback_data=f"roles:info:{role.value}")
        for role in ROLE_INFO_ORDER
    ]
    for index in range(0, len(buttons), 2):
        rows.append(buttons[index:index + 2])
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="start:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def role_info_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Ortga", callback_data="roles:list")],
            [InlineKeyboardButton(text="🏠 User panel", callback_data="start:back")],
        ]
    )


def commands_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👤 Profil", callback_data="profile:open"),
                InlineKeyboardButton(text="🃏 Qoidalar", callback_data="rules:show"),
            ],
            [
                InlineKeyboardButton(text="💵 Dollar", callback_data="dollar:shop"),
                InlineKeyboardButton(text="💎 Almaz", callback_data="diamond:shop"),
            ],
            [InlineKeyboardButton(text="◀️ User panel", callback_data="profile:open")],
        ]
    )


def lobby_keyboard(
    lang: str,
    game_id: int,
    bot_username: str,
    chat_id: int,
    active: bool = True,
    tournament: bool = False,
) -> Optional[InlineKeyboardMarkup]:
    if not active:
        return None
    if tournament:
        blue_link = f"https://t.me/{_clean_bot_username(bot_username)}?start=jointeam_{game_id}_{chat_id}_blue"
        red_link = f"https://t.me/{_clean_bot_username(bot_username)}?start=jointeam_{game_id}_{chat_id}_red"
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔵 Qo'shilish", url=blue_link),
                    InlineKeyboardButton(text="🔴 Qo'shilish", url=red_link),
                ]
            ]
        )
    deep_link = f"https://t.me/{_clean_bot_username(bot_username)}?start=join_{game_id}_{chat_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t(lang, "join_btn"), url=deep_link)]]
    )


def go_private_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Bot-ga o'tish ↗", url=f"https://t.me/{_clean_bot_username(settings.bot_username)}")],
        ]
    )


def go_role_private_keyboard(settings: Settings, game_id: int, text: str = "Rol haqida") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    url=f"https://t.me/{_clean_bot_username(settings.bot_username)}",
                )
            ],
        ]
    )


def go_vote_private_keyboard(settings: Settings, game_id: int) -> InlineKeyboardMarkup:
    bot_username = _clean_bot_username(settings.bot_username)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"@{bot_username}",
                    url=f"https://t.me/{bot_username}",
                )
            ],
        ]
    )


def group_url_from_chat_id(chat_id: int) -> str:
    internal_id = str(chat_id)
    if internal_id.startswith("-100"):
        internal_id = internal_id[4:]
    elif internal_id.startswith("-"):
        internal_id = internal_id[1:]
    return f"https://t.me/c/{internal_id}"


def go_group_keyboard(chat_id: int, group_url: Optional[str] = None) -> InlineKeyboardMarkup:
    url = group_url or group_url_from_chat_id(chat_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Guruhga o'tish", url=url)],
        ]
    )


def target_keyboard(prefix: str, game_id: int, actor_id: int, choices: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=name,
                callback_data=f"act:{prefix}:{game_id}:{actor_id}:{target_id}",
            )
        ]
        for target_id, name in choices
    ]
    rows.append([InlineKeyboardButton(text="O'tkazib yuborish", callback_data=f"skip:night:{game_id}:{actor_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def joker_death_card_keyboard(game_id: int, actor_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🃏 {JOKER_CARD_LABELS[idx]}", callback_data=f"act:joker_card:{game_id}:{actor_id}:{idx}")]
        for idx in (1, 2, 3, 4)
    ]
    rows.append([InlineKeyboardButton(text="O'tkazib yuborish", callback_data=f"skip:night:{game_id}:{actor_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def joker_target_keyboard(game_id: int, actor_id: int, choices: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=name, callback_data=f"act:joker_target:{game_id}:{actor_id}:{target_id}")]
        for target_id, name in choices
    ]
    rows.append([InlineKeyboardButton(text="O'tkazib yuborish", callback_data=f"skip:night:{game_id}:{actor_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def joker_victim_card_keyboard(game_id: int, target_id: int, actor_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🃏 {JOKER_CARD_LABELS[idx]}", callback_data=f"jokerpick:{game_id}:{target_id}:{actor_id}:{idx}")]
        for idx in (1, 2, 3, 4)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sorcerer_hang_revenge_keyboard(
    game_id: int,
    sorcerer_id: int,
    choices: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=name,
                callback_data=f"sorhang:{game_id}:{sorcerer_id}:{target_id}",
            )
        ]
        for target_id, name in choices
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sorcerer_judgement_keyboard(
    game_id: int,
    sorcerer_id: int,
    attacker_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Kechirish",
                    callback_data=f"sorjudge:{game_id}:{sorcerer_id}:{attacker_id}:forgive",
                ),
                InlineKeyboardButton(
                    text="💀 Oldirish",
                    callback_data=f"sorjudge:{game_id}:{sorcerer_id}:{attacker_id}:kill",
                ),
            ]
        ]
    )


def miner_keyboard(game_id: int, actor_id: int, visited_mines: set[int] | None = None) -> InlineKeyboardMarkup:
    visited_mines = visited_mines or set()
    rows = []
    for start in (1, 6):
        row = []
        for mine in range(start, start + 5):
            if mine in visited_mines:
                continue
            row.append(
                InlineKeyboardButton(
                    text=f"{mine:02d}",
                    callback_data=f"act:mine:{game_id}:{actor_id}:{mine}",
                )
            )
        if row:
            rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                text="⚜️ Himoyalanish",
                callback_data=f"act:mine_protect:{game_id}:{actor_id}:0",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="O'tkazib yuborish", callback_data=f"skip:night:{game_id}:{actor_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def commissar_action_keyboard(game_id: int, actor_id: int, can_shoot: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Tekshirish",
                callback_data=f"commissar:check:{game_id}:{actor_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="Otish",
                callback_data=f"commissar:shoot:{game_id}:{actor_id}",
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def commissar_target_keyboard(
    action_key: str,
    game_id: int,
    actor_id: int,
    choices: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    kb = target_keyboard(action_key, game_id, actor_id, choices)
    kb.inline_keyboard.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data=f"commissar:menu:{game_id}:{actor_id}")])
    return kb


def vote_keyboard(game_id: int, choices: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
            [InlineKeyboardButton(text=name, callback_data=f"vote:{game_id}:{target_id}")]
            for target_id, name in choices
        ]
    rows.append([InlineKeyboardButton(text="O'tkazib yuborish", callback_data=f"skip:vote:{game_id}:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_hang_keyboard(game_id: int, target_id: int, yes_count: int = 0, no_count: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"👍 {yes_count}", callback_data=f"hang:yes:{game_id}:{target_id}"),
                InlineKeyboardButton(text=f"👎 {no_count}", callback_data=f"hang:no:{game_id}:{target_id}"),
            ],
        ]
    )


def judge_cancel_keyboard(game_id: int, target_id: int, judge_id: int, confirm_message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧑‍⚖️ Osishni bekor qilish",
                    callback_data=f"judgecancel:{game_id}:{target_id}:{judge_id}:{confirm_message_id}",
                )
            ],
            [InlineKeyboardButton(text="O'tkazib yuborish", callback_data=f"skip:judge:{game_id}:{judge_id}")],
        ]
    )


def settings_keyboard(lang: str, game_id: Optional[int] = None) -> InlineKeyboardMarkup:
    def callback(action: str) -> str:
        return f"settings:{game_id}:{action}" if game_id is not None else f"settings:{action}"

    items = [
        (callback("lang"), "🌍 Til sozlamasi"),
        (callback("timeout"), "⏳ Registration timeout"),
        (callback("minplayers"), "👥 Minimum players"),
        (callback("roles"), "🎭 Role settings"),
        (callback("welcome"), "👋 Salomlashuv"),
        (callback("premium"), "🎲 Premium status"),
        (callback("logs"), "🧾 Game logs"),
        (callback("media"), "🖼 Day/Night media"),
        (callback("stop"), "🛑 Stop game"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=cb)] for cb, label in items]
    )


def group_welcome_keyboard(chat_id: int, enabled: bool, has_media: bool) -> InlineKeyboardMarkup:
    def callback(action: str) -> str:
        return f"settings:{chat_id}:{action}"

    rows = [
        [
            InlineKeyboardButton(
                text="🔴 O'chirish" if enabled else "🟢 Yoqish",
                callback_data=callback("welcome_toggle"),
            )
        ],
        [InlineKeyboardButton(text="✏️ Matnni o'zgartirish", callback_data=callback("welcome_text"))],
        [InlineKeyboardButton(text="🖼 Media qo'shish / o'zgartirish", callback_data=callback("welcome_media"))],
    ]
    if has_media:
        rows.append([InlineKeyboardButton(text="🗑 Mediani o'chirish", callback_data=callback("welcome_media_clear"))])
    rows.append([InlineKeyboardButton(text="◀️ Settings", callback_data=callback("back"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def role_preset_keyboard(current_preset: str = "black23", chat_id: Optional[int] = None) -> InlineKeyboardMarkup:
    if current_preset in {"black23", "extended35"}:
        current_preset = "classic"

    def label(preset: str, text: str) -> str:
        return f"✅ {text}" if current_preset == preset else text

    def callback(action: str) -> str:
        return f"settings:{chat_id}:{action}" if chat_id is not None else f"settings:{action}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label("classic", "🎭 Classic"), callback_data=callback("rolepreset:classic"))],
            [InlineKeyboardButton(text=label("super", "⚡ Super"), callback_data=callback("rolepreset:super"))],
            [InlineKeyboardButton(text=label("mega", "🔥 Mega"), callback_data=callback("rolepreset:mega"))],
            [InlineKeyboardButton(text="◀️ Settings", callback_data=callback("back"))],
        ]
    )


def roles_overview_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=role_label(r), callback_data="noop") ] for r in Role]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def shop_keyboard(has_hero: bool = False) -> InlineKeyboardMarkup:
    hero_button = (
        InlineKeyboardButton(text="🥷 Geroyim", callback_data="hero:panel")
        if has_hero
        else InlineKeyboardButton(text="🥷 Geroy sotib olish - 100💎", callback_data="hero:shop:buy")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [hero_button],
            [InlineKeyboardButton(text="🛡 Himoya - 100💵", callback_data="shop:buy:protection")],
            [InlineKeyboardButton(text="⚖️ Ovozdan himoya - 1💎", callback_data="shop:buy:vote_protection")],
            [InlineKeyboardButton(text="💊 Doridan himoya - 100💵", callback_data="shop:buy:drug_protection")],
            [InlineKeyboardButton(text="🎭 Maska - 100💵", callback_data="shop:buy:mask")],
            [InlineKeyboardButton(text="🧿 Qotildan himoya - 2💎", callback_data="shop:buy:killer_protection")],
            [InlineKeyboardButton(text="📦 Sirpanishdan himoya - 300💵", callback_data="shop:buy:miner_protection")],
            [InlineKeyboardButton(text="🃏 Keyingi rol tanlash", callback_data="shop:roles")],
            [InlineKeyboardButton(text="👑 VIP User", callback_data="vip:open")],
            [InlineKeyboardButton(text="🎁 Telegram sovg'asiga almashtirish", callback_data="shop:gifts")],
            [InlineKeyboardButton(text="◀️ Orqaga", callback_data="profile:open")],
        ]
    )


def vip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 30 almaz bilan faollashtirish", callback_data="vip:buy:diamonds")],
            [InlineKeyboardButton(text="⭐ 190 stars bilan faollashtirish", callback_data="vip:buy:stars")],
            [InlineKeyboardButton(text="🎁 Oddiy Keys", callback_data="box:info:normal")],
            [InlineKeyboardButton(text="🧰 Super Keys", callback_data="box:info:super")],
            [InlineKeyboardButton(text="👑 Mega Quti", callback_data="box:info:mega")],
            [InlineKeyboardButton(text="◀️ Orqaga", callback_data="shop:open")],
        ]
    )


def box_info_keyboard(
    box_type: str,
    can_paid_open: bool = False,
    paid_open_cost: int = 5000,
) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🎁 Ochish", callback_data=f"box:open:{box_type}")]]
    if can_paid_open:
        rows.append([InlineKeyboardButton(text=f"💵 {paid_open_cost} evaziga ochish", callback_data=f"box:open_paid:{box_type}")])
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="vip:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def box_pick_keyboard(box_type: str, session_id: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    idx = 1
    for _ in range(4):
        row: list[InlineKeyboardButton] = []
        for _ in range(4):
            row.append(
                InlineKeyboardButton(
                    text=f"🎁 {idx}",
                    callback_data=f"box:pick:{box_type}:{session_id}:{idx}",
                )
            )
            idx += 1
        rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data=f"box:info:{box_type}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_GIFT_TIER_EMOJI = [
    (15, "💝"),
    (25, "🌹"),
    (50, "🎂"),
    (100, "🧸"),
    (250, "🍾"),
    (500, "💎"),
    (1000, "👑"),
    (2500, "🏆"),
    (5000, "💍"),
    (10000, "💖"),
]


def _pick_gift_emoji(sticker_emoji: str | None, stars: int) -> str:
    # Prefer a real plain-emoji from sticker if present and not a generic gift-box.
    if sticker_emoji and sticker_emoji not in {"🎁", "📦"}:
        return sticker_emoji
    fallback = "🎁"
    for threshold, em in _GIFT_TIER_EMOJI:
        if stars <= threshold:
            return em
        fallback = em
    return fallback


_GIFT_FIXED_PRICES: dict[int, int] = {
    15: 8,
    25: 11,
    50: 15,
    100: 28,
}


def gift_shop_keyboard(gifts: list[object], stars_per_diamond: int) -> InlineKeyboardMarkup:
    import math
    rows: list[list[InlineKeyboardButton]] = []
    for gift in gifts:
        gift_id = getattr(gift, "id", None)
        stars = int(getattr(gift, "star_count", 0) or 0)
        if not gift_id or stars <= 0:
            continue
        diamonds = _GIFT_FIXED_PRICES.get(stars) or max(1, math.ceil(stars / stars_per_diamond))
        remaining = getattr(gift, "remaining_count", None)
        total = getattr(gift, "total_count", None)
        suffix = ""
        if remaining is not None and total is not None:
            suffix = f"  ({remaining}/{total})"
        sticker = getattr(gift, "sticker", None)
        emoji = _pick_gift_emoji(getattr(sticker, "emoji", None), stars)
        rows.append([
            InlineKeyboardButton(
                text=f"{emoji} {stars}⭐ — {diamonds}💎{suffix}",
                callback_data=f"gift:buy:{gift_id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="👑 Telegram Premium", callback_data="gift:premium")])
    rows.append([InlineKeyboardButton(text="🔄 Yangilash", callback_data="shop:gifts")])
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="shop:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def premium_shop_keyboard(plans: list[tuple[int, int, int]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for months, stars, diamonds in plans:
        rows.append([
            InlineKeyboardButton(
                text=f"👑 {months} oy — {diamonds}💎",
                callback_data=f"gift:premium:buy:{months}",
            )
        ])
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="shop:gifts")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def premium_confirm_keyboard(months: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"gift:premium:confirm:{months}")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="gift:premium")],
        ]
    )


def gift_confirm_keyboard(gift_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"gift:confirm:{gift_id}")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="shop:gifts")],
        ]
    )


def hero_panel_keyboard(is_for_sale: bool = False) -> InlineKeyboardMarkup:
    sale_rows = (
        [
            [InlineKeyboardButton(text="❌ Sotuvdan qaytarish", callback_data="hero:sale:cancel")],
            [InlineKeyboardButton(text="✏️ Narxni o'zgartirish", callback_data="hero:sale:price")],
        ]
        if is_for_sale
        else [[InlineKeyboardButton(text="🏷 Geroyni sotish", callback_data="hero:sell")]]
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ 1000 Ball qo'shish", callback_data="hero:add_points")],
            [InlineKeyboardButton(text="🛡 Himoyani yangilash", callback_data="hero:upgrade_def")],
            [InlineKeyboardButton(text="🩸 Qurolni zaryadlash", callback_data="hero:recharge")],
            [InlineKeyboardButton(text="🖋 Nomini o'zgartirish", callback_data="hero:rename")],
            *sale_rows,
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="shop:open")],
        ]
    )


def hero_game_keyboard(can_attack: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if can_attack:
        rows.append([InlineKeyboardButton(text="⚔️ Zarba berish", callback_data="hero:game:attack")])
    rows.extend(
        [
            [InlineKeyboardButton(text="🛡 Himoyalanish", callback_data="hero:game:defend")],
            [InlineKeyboardButton(text="📊 Jonlar holati", callback_data="hero:game:hp")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="hero:game:cancel")],
        ]
    )
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def hero_target_keyboard(players: list[object]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=getattr(player, "display_name", "User"), callback_data=f"hero:game:target:{player.id}")]
        for player in players
    ]
    rows.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="hero:game:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hero_defense_keyboard(max_amount: int) -> InlineKeyboardMarkup:
    choices = [amount for amount in (10, 20, 30) if amount <= max_amount]
    rows = [[InlineKeyboardButton(text=str(amount), callback_data=f"hero:game:defamount:{amount}")] for amount in choices]
    rows.append([InlineKeyboardButton(text="Maksimal", callback_data="hero:game:defamount:max")])
    rows.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="hero:game:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hero_damage_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Maksimal zarba", callback_data="hero:game:damage:max")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="hero:game:attack")],
        ]
    )


def hero_market_buy_keyboard(hero_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Sotib olish", callback_data=f"hero:market:buy:{hero_id}")],
        ]
    )


def owner_hero_market_keyboard(has_channel: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Qo'shish / o'zgartirish", callback_data="owner:hero_market_set")]]
    if has_channel:
        rows.append([InlineKeyboardButton(text="🗑 O'chirish", callback_data="owner:hero_market_clear")])
    rows.append([InlineKeyboardButton(text="◀️ Admin panel", callback_data="owner:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def role_shop_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for item in SHOP_ROLE_CATALOG:
        icon = "💎" if item.currency == "diamonds" else "💵"
        rows.append([
            InlineKeyboardButton(
                text=f"{role_label(item.role)} - {item.price}{icon}",
                callback_data=f"shop:role:{item.role.value}",
            )
        ])
    rows.append([InlineKeyboardButton(text="🎒 Mening rollarim", callback_data="shop:my_roles")])
    rows.append([InlineKeyboardButton(text="🚫 Faol rolni o'chirish - 100💵", callback_data="shop:disable_roles")])
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="shop:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def my_roles_keyboard(roles: list[str], selected_role: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for role_value in roles:
        mark = "✅ " if selected_role == role_value else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{mark}{role_label(role_value)}",
                callback_data=f"shop:my_role:{role_value}",
            )
        ])
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="shop:roles")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def disable_role_shop_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🚫 {role_label(role)} - 100💵", callback_data=f"shop:disable_role:{role.value}")]
        for role in ACTIVE_ROLE_POOL
        if role not in {Role.CITIZEN, Role.DON}
    ]
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="shop:roles")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dollar_exchange_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💎 1 → 💵 500", callback_data="dollar:exchange:1"),
                InlineKeyboardButton(text="💎 5 → 💵 2500", callback_data="dollar:exchange:5"),
            ],
            [
                InlineKeyboardButton(text="💎 10 → 💵 5000", callback_data="dollar:exchange:10"),
                InlineKeyboardButton(text="💎 50 → 💵 25000", callback_data="dollar:exchange:50"),
            ],
            [InlineKeyboardButton(text="💎 Hammasini almashtirish", callback_data="dollar:exchange:all")],
            [InlineKeyboardButton(text="◀️ Orqaga", callback_data="profile:open")],
        ]
    )


def diamond_shop_keyboard(admin_username: str) -> InlineKeyboardMarkup:
    admin_url = f"https://t.me/{_clean_bot_username(admin_username)}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💎 1 - ⭐ 7", callback_data="diamond:buy:1"),
                InlineKeyboardButton(text="💎 10 - ⭐ 70", callback_data="diamond:buy:10"),
            ],
            [
                InlineKeyboardButton(text="💎 30 - ⭐ 200", callback_data="diamond:buy:30"),
                InlineKeyboardButton(text="💎 70 - ⭐ 450", callback_data="diamond:buy:70"),
            ],
            [
                InlineKeyboardButton(text="💎 250 - ⭐ 1300", callback_data="diamond:buy:250"),
                InlineKeyboardButton(text="💎 1000 - ⭐ 5000", callback_data="diamond:buy:1000"),
            ],
            [
                InlineKeyboardButton(text="👤 Admin orqali", url=admin_url),
                InlineKeyboardButton(text="◀️ Orqaga", callback_data="profile:open"),
            ],
        ]
    )


def owner_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Statistika", callback_data="owner:stats")],
            [InlineKeyboardButton(text="💎 TOP 30 almaz", callback_data="owner:diamond_top")],
            [InlineKeyboardButton(text="💎 Almaz loglari", callback_data="owner:diamond_audit")],
            [InlineKeyboardButton(text="🏠 Admin guruh", callback_data="owner:admin_group")],
            [InlineKeyboardButton(text="🎲 Premium guruhlar", callback_data="owner:premium_groups")],
            [InlineKeyboardButton(text="🚷 Blacklist", callback_data="owner:premium_blocked_list")],
            [InlineKeyboardButton(text=" Xarid admini", callback_data="owner:purchase_admin")],
            [InlineKeyboardButton(text="📰 Yangiliklar kanali", callback_data="owner:news_channel")],
            [InlineKeyboardButton(text="📺 Kanal sovg'a balansi", callback_data="owner:channel_gifts")],
            [InlineKeyboardButton(text="🥷 Geroy savdo kanali", callback_data="owner:hero_market_channel")],
            [InlineKeyboardButton(text="📣 Userlarga reklama", callback_data="owner:broadcast_users")],
            [InlineKeyboardButton(text="🏘 Guruhlarga reklama", callback_data="owner:broadcast_groups")],
            [InlineKeyboardButton(text="🎁 Kredit berish", callback_data="owner:grant_help")],
            [InlineKeyboardButton(text="🧾 Almaz invoice", callback_data="owner:invoice")],
            [InlineKeyboardButton(text="📋 Barcha buyruqlar", callback_data="owner:commands")],
            [InlineKeyboardButton(text="🧾 Yordam", callback_data="owner:help")],
            [InlineKeyboardButton(text="◀️ User panel", callback_data="start:back")],
        ]
    )

def owner_channel_gifts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Almaz tarqatishni boshlash", callback_data="owner:channel_gifts:start")],
            [InlineKeyboardButton(text="📊 Kanal balansini ko'rish", callback_data="owner:channel_gifts:view")],
            [InlineKeyboardButton(text="➕ Kanal balansini to'ldirish", callback_data="owner:channel_gifts:grant")],
            [InlineKeyboardButton(text="◀️ Admin panel", callback_data="owner:panel")],
        ]
    )


def owner_channel_gift_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Tez tarqatish", callback_data="owner:channel_gifts:mode:send")],
            [InlineKeyboardButton(text="🎲 Ro'yxatdan o'tish", callback_data="owner:channel_gifts:mode:change")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="owner:cancel")],
        ]
    )


def owner_diamond_audit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash", callback_data="owner:diamond_audit")],
            [InlineKeyboardButton(text="🏠 Log guruhini ulash", callback_data="owner:admin_group")],
            [InlineKeyboardButton(text="◀️ Admin panel", callback_data="owner:panel")],
        ]
    )


def owner_diamond_top_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash", callback_data="owner:diamond_top")],
            [InlineKeyboardButton(text="💎 Almaz loglari", callback_data="owner:diamond_audit")],
            [InlineKeyboardButton(text="◀️ Admin panel", callback_data="owner:panel")],
        ]
    )


def owner_admin_group_keyboard(has_group: bool, can_use_current_chat: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if can_use_current_chat:
        rows.append([InlineKeyboardButton(text="✅ Shu guruhni ulash", callback_data="owner:admin_group:current")])
    rows.append([InlineKeyboardButton(text="✏️ ID bilan ulash", callback_data="owner:admin_group:set")])
    if has_group:
        rows.append([InlineKeyboardButton(text="🗑 O'chirish", callback_data="owner:admin_group:clear")])
    rows.append([InlineKeyboardButton(text="◀️ Admin panel", callback_data="owner:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def owner_invoice_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Yangi invoice yaratish", callback_data="owner:invoice:new")],
            [InlineKeyboardButton(text="◀️ Admin panel", callback_data="owner:panel")],
        ]
    )


def owner_invoice_delivery_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Sharable link yaratish", callback_data="owner:invoice:make_link")],
            [InlineKeyboardButton(text="📤 Aniq userga yuborish", callback_data="owner:invoice:make_send")],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="owner:invoice")],
        ]
    )


def owner_invoice_after_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Yana invoice yaratish", callback_data="owner:invoice:new")],
            [InlineKeyboardButton(text="◀️ Admin panel", callback_data="owner:panel")],
        ]
    )


def owner_wait_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="owner:cancel")],
        ]
    )


def owner_news_channel_keyboard(has_link: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Qo'shish / o'zgartirish", callback_data="owner:news_set")]]
    if has_link:
        rows.append([InlineKeyboardButton(text="🗑 O'chirish", callback_data="owner:news_clear")])
    rows.append([InlineKeyboardButton(text="◀️ Admin panel", callback_data="owner:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def owner_premium_groups_keyboard(groups: list[object] | None = None) -> InlineKeyboardMarkup:
    rows = []
    for group in groups or []:
        total = getattr(group, "total_diamonds", 0) or 0
        if total <= 0:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{group.title} - 💎 {total}",
                    callback_data=f"owner:premium_bankrupt:{group.id}",
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="📋 Premium guruhlar ro'yxati", callback_data="owner:premium_list")],
            [InlineKeyboardButton(text="⏱ Premium timer", callback_data="owner:premium_timer")],
            [
                InlineKeyboardButton(text="🚫 Userni bloklash", callback_data="owner:premium_block_user"),
                InlineKeyboardButton(text="✅ Blokdan chiqarish", callback_data="owner:premium_unblock_user"),
            ],
            [InlineKeyboardButton(text="🚷 Bloklanganlar", callback_data="owner:premium_blocked_list")],
            [InlineKeyboardButton(text="◀️ Admin panel", callback_data="owner:panel")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def premium_groups_keyboard(groups: list[object]) -> InlineKeyboardMarkup:
    rows = []
    for group in groups:
        total = getattr(group, "total_diamonds", None) or getattr(group, "diamond_price", 0)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{group.title} - 💎 {total}",
                    url=group.invite_link,
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="start:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def _back_btn(back_to: str) -> InlineKeyboardButton:
    return _btn("⬅️ Orqaga", f"settings:back:{back_to}")


def _exit_btn() -> InlineKeyboardButton:
    return _btn("❌ Chiqish", "settings:exit")


def _back_exit_row(back_to: str) -> list[InlineKeyboardButton]:
    return [_back_btn(back_to), _exit_btn()]


def settings_main_keyboard() -> InlineKeyboardMarkup:
    items = [
        ("🎁 Giveawaylar", "settings:giveaway"),
        ("⏰ Vaqtlar", "settings:times"),
        ("🎭 Rollar", "settings:roles"),
        ("🔫 Qurollar", "settings:weapons"),
        ("🚪 Leave qilish", "settings:leave"),
        ("🔐 Buyruqlarga ruxsatlar", "settings:permissions"),
        ("✍️ Yozishni cheklash", "settings:chat"),
        ("🎮 O'yin modi", "settings:mode"),
        ("⚙️ Boshqa sozlamalar", "settings:extra"),
        ("📊 Boshqaruv paneli", "settings:panel"),
    ]
    rows = [[_btn(label, cb)] for label, cb in items]
    rows.append([_exit_btn()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_giveaway_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("💎 Olmoslar", "settings:giveaway:diamond")],
        [_btn("🛡 Himoyalar", "settings:giveaway:protection")],
        _back_exit_row("main"),
    ])


def settings_giveaway_amount_keyboard(gtype: str, current: int = 0) -> InlineKeyboardMarkup:
    amounts = [0, 10, 20, 30, 40, 50]
    rows = []
    for a in amounts:
        mark = "✅ " if a == current else ""
        rows.append([_btn(f"{mark}{a}", f"settings:giveaway:{gtype}:{a}")])
    rows.append(_back_exit_row("giveaway"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_roles_keyboard(states: dict[str, bool] | None = None) -> InlineKeyboardMarkup:
    roles = [
        ("🤵🏻 Don", "don"),
        ("🤵🏼 Mafia", "mafia"),
        ("🕵🏼 Komissar Katani", "commissar_katani"),
        ("👨🏼‍⚕️ Doktor", "doctor"),
        ("👮🏼 Serjant", "sergeant"),
        ("🎖 Janob", "gentleman"),
        ("👨🏼 Tinch aholi", "citizen"),
        ("🧙‍♂️ Daydi", "wanderer"),
        ("💃 Kezuvchi", "traveler"),
        ("👨🏼‍💼 Advokat", "lawyer"),
        ("🤦 Suidsid", "suicide"),
        ("🤞 Omadli", "lucky"),
        ("🐺 Bo'ri", "wolf"),
        ("🔪 Qotil", "killer"),
        ("🥷 Yollanma qotil", "mercenary_killer"),
        ("💣 Afsungar", "sorcerer"),
        ("🃏 Aferist", "swindler"),
        ("🧙 Sehrgar", "magician"),
        ("🧟 G'azabkor", "angry"),
        ("📰 Jurnalist", "journalist"),
        ("😎 Sotqin", "traitor"),
        ("🧪 Kimyogar", "chemist"),
        ("🛡 Qo'riqchi", "guard"),
        ("😂 Hazilkash", "prankster"),
        ("🃏 Joker", "joker"),
    ]
    rows = []
    for label, key in roles:
        allowed = (states or {}).get(key, True)
        mark = "✅" if allowed else "🚫"
        rows.append([_btn(f"{mark} {label}", f"settings:role:{key}")])
    rows.append(_back_exit_row("main"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_role_toggle_keyboard(role_key: str, is_allowed: bool = True) -> InlineKeyboardMarkup:
    ban_mark = "✅ " if not is_allowed else ""
    allow_mark = "✅ " if is_allowed else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"{ban_mark}🚫 Taqiqlash", f"settings:role:{role_key}:ban")],
        [_btn(f"{allow_mark}✅ Ruxsat berish", f"settings:role:{role_key}:allow")],
        _back_exit_row("roles"),
    ])


def settings_weapons_keyboard(states: dict[str, bool] | None = None) -> InlineKeyboardMarkup:
    weapons = [
        ("🛡 Himoya", "protection"),
        ("📁 Hujjat", "document"),
        ("🚨 Qotildan himoya", "killer_protection"),
        ("⚖️ Ovozdan himoya", "vote_protection"),
        ("🔫 Miltiq", "gun"),
        ("💊 Doridan himoya", "medicine_protection"),
        ("📦 Sirpanishdan himoya", "slip_protection"),
        ("🎭 Maska", "mask"),
        ("🥷 Geroy", "hero"),
        ("🃏 Faol rol", "active_role"),
    ]
    rows = []
    for label, key in weapons:
        enabled = (states or {}).get(key, True)
        mark = "✅" if enabled else "🚫"
        rows.append([_btn(f"{mark} {label}", f"settings:weapon:{key}")])
    rows.append(_back_exit_row("main"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_weapon_toggle_keyboard(weapon_key: str, is_enabled: bool = True) -> InlineKeyboardMarkup:
    on_mark = "✅ " if is_enabled else ""
    off_mark = "✅ " if not is_enabled else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"{on_mark}✅ Yoqish", f"settings:weapon:{weapon_key}:on")],
        [_btn(f"{off_mark}🚫 O'chirish", f"settings:weapon:{weapon_key}:off")],
        _back_exit_row("weapons"),
    ])


def settings_leave_keyboard(current: bool = True, lock_minutes: int = 30) -> InlineKeyboardMarkup:
    on_mark = "✅ " if current else ""
    off_mark = "✅ " if not current else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"{on_mark}Ha - Ruxsat", "settings:leave:on")],
        [_btn(f"{off_mark}Yo'q - Taqiq", "settings:leave:off")],
        [_btn(f"⏱ Blok vaqti: {lock_minutes} daqiqa", "settings:leave:lock")],
        _back_exit_row("main"),
    ])


def settings_leave_lock_keyboard(current_minutes: int = 30) -> InlineKeyboardMarkup:
    options = [0, 5, 10, 15, 30, 60, 120]
    rows = []
    for value in options:
        mark = "✅ " if value == current_minutes else ""
        label = "O'chirish (0)" if value == 0 else f"{value} daqiqa"
        rows.append([_btn(f"{mark}{label}", f"settings:leave:lock:{value}")])
    rows.append(_back_exit_row("leave"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


PERMISSION_ICONS = {"owner": "👑", "admin": "🛡", "user": "👥"}


def settings_permissions_keyboard(states: dict[str, str] | None = None) -> InlineKeyboardMarkup:
    commands = [
        ("start", "start"),
        ("stop", "stop"),
        ("game", "game"),
        ("Top 1", "top_1"),
        ("Top 7", "top_7"),
        ("Top 30", "top_30"),
        ("Taqdirlash Top 1", "reward_top_1"),
        ("Taqdirlash Top 7", "reward_top_7"),
        ("Taqdirlash Top 30", "reward_top_30"),
    ]
    rows = []
    for label, key in commands:
        level = (states or {}).get(key, "user")
        icon = PERMISSION_ICONS.get(level, "👥")
        rows.append([_btn(f"{icon} {label}", f"settings:permission:{key}")])
    rows.append(_back_exit_row("main"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_permission_level_keyboard(cmd_key: str, current: str = "user") -> InlineKeyboardMarkup:
    levels = [("👑 Ega", "owner"), ("🛡 Admin", "admin"), ("👥 Obunachilar", "user")]
    rows = []
    for label, val in levels:
        mark = "✅ " if val == current else ""
        rows.append([_btn(f"{mark}{label}", f"settings:permission:{cmd_key}:{val}")])
    rows.append(_back_exit_row("permissions"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_chat_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🌙 Tun", "settings:chat:night")],
        [_btn("☀️ Kun", "settings:chat:day")],
        _back_exit_row("main"),
    ])


def settings_chat_phase_keyboard(phase: str, current: str = "") -> InlineKeyboardMarkup:
    options = [
        ("👑 Faqat Ega", "owner"),
        ("🛡 Faqat Adminlar", "admin"),
        ("💚 Faqat tirik ishtirokchilar", "alive_players"),
        ("🎮 Faqat ishtirokchilar", "players"),
        ("👥 Hamma", "all"),
    ]
    rows = []
    for label, val in options:
        mark = "✅ " if val == current else ""
        rows.append([_btn(f"{mark}{label}", f"settings:chat:{phase}:{val}")])
    rows.append(_back_exit_row("chat"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_times_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🌙 Tun vaqti", "settings:time:night_time")],
        [_btn("☀️ Kun vaqti", "settings:time:day_time")],
        [_btn("🗳 Ovoz berish", "settings:time:vote_time")],
        [_btn("⏳ Ro'yxatdan o'tish", "settings:time:registration_time")],
        [_btn("🛡 Admin tasdiqi", "settings:time:admin_start_confirm")],
        _back_exit_row("main"),
    ])


def settings_time_value_keyboard(time_key: str, current: int = 0) -> InlineKeyboardMarkup:
    values = [30, 45, 60, 90, 120, 180, 300]
    rows = []
    for v in values:
        mark = "✅ " if v == current else ""
        rows.append([_btn(f"{mark}{v} soniya", f"settings:time:{time_key}:{v}")])
    rows.append(_back_exit_row("times"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_admin_confirm_keyboard(enabled: bool = False) -> InlineKeyboardMarkup:
    on_mark = "✅ " if enabled else ""
    off_mark = "✅ " if not enabled else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"{on_mark}✅ Yoqish", "settings:time:admin_start_confirm:on")],
        [_btn(f"{off_mark}🚫 O'chirish", "settings:time:admin_start_confirm:off")],
        _back_exit_row("times"),
    ])


def settings_mode_keyboard(current: str = "normal") -> InlineKeyboardMarkup:
    modes = [("🎲 Oddiy", "normal"), ("⚡ Tezkor", "fast"), ("🛡 Himoyali", "protected"), ("🔥 Qiyin", "hard")]
    rows = []
    for label, val in modes:
        mark = "✅ " if val == current else ""
        rows.append([_btn(f"{mark}{label}", f"settings:mode:{val}")])
    rows.append(_back_exit_row("main"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_extra_keyboard(states: dict[str, bool] | None = None) -> InlineKeyboardMarkup:
    extras = [
        ("🔔 Bildirishnoma", "notifications"),
        ("🗑 Avto tozalash", "auto_clean"),
        ("📌 Pin xabar", "pin_message"),
        ("📢 Natija e'loni", "result_announce"),
    ]
    rows = []
    for label, key in extras:
        enabled = (states or {}).get(key, True)
        mark = "✅" if enabled else "🚫"
        rows.append([_btn(f"{mark} {label}", f"settings:extra:{key}")])
    rows.append(_back_exit_row("main"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_extra_toggle_keyboard(extra_key: str, is_enabled: bool = True) -> InlineKeyboardMarkup:
    on_mark = "✅ " if is_enabled else ""
    off_mark = "✅ " if not is_enabled else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"{on_mark}✅ Yoqish", f"settings:extra:{extra_key}:on")],
        [_btn(f"{off_mark}🚫 O'chirish", f"settings:extra:{extra_key}:off")],
        _back_exit_row("extra"),
    ])


def settings_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_back_exit_row("main")])
