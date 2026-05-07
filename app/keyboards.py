from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import Settings
from app.enums import Role
from app.roles import role_label
from app.texts import t

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


def language_keyboard(scope: str = "user", chat_id: int | None = None) -> InlineKeyboardMarkup:
    rows = []
    for code, label in LANGS:
        suffix = f"{scope}:{code}:{chat_id or 0}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"lang:{suffix}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def start_menu_keyboard(lang: str, settings: Settings, is_admin: bool = False) -> InlineKeyboardMarkup:
    add_url = f"https://t.me/{_clean_bot_username(settings.bot_username)}?startgroup=true"
    rows = [
        [InlineKeyboardButton(text=t(lang, "add_to_group"), url=add_url)],
        [InlineKeyboardButton(text=t(lang, "premium_groups"), callback_data="premium:info")],
        [InlineKeyboardButton(text="🛒 Do'kon", callback_data="shop:open")],
        [InlineKeyboardButton(text=t(lang, "news"), url=settings.news_channel_url)],
        [
            InlineKeyboardButton(text=t(lang, "lang"), callback_data="lang:menu:user:0"),
            InlineKeyboardButton(text=t(lang, "rules_btn"), callback_data="rules:show"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛡 Admin panel", callback_data="owner:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_dashboard_keyboard(settings: Settings, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="📁 ...ON", callback_data="noop"),
            InlineKeyboardButton(text="🛡 ...ON", callback_data="noop"),
            InlineKeyboardButton(text="🎭 ...ON", callback_data="noop"),
        ],
        [
            InlineKeyboardButton(text="🔫 - 🟢 ON", callback_data="noop"),
            InlineKeyboardButton(text="⚖️ - 🟢 ON", callback_data="noop"),
        ],
        [InlineKeyboardButton(text="Do'kon", callback_data="shop:open")],
        [
            InlineKeyboardButton(text="Xarid qilish 💵", callback_data="shop:open"),
            InlineKeyboardButton(text="Xarid qilish 💎", callback_data="premium:info"),
        ],
        [
            InlineKeyboardButton(text="🌍 Til", callback_data="lang:menu:user:0"),
            InlineKeyboardButton(text="🃏 O'yin qoidalari", callback_data="rules:show"),
        ],
        [InlineKeyboardButton(text="🎲 Premium guruhlar", callback_data="premium:info")],
        [InlineKeyboardButton(text="Yangiliklar ↗", url=settings.news_channel_url)],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛡 Admin panel", callback_data="owner:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rules_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Orqaga", callback_data="start:back")],
        ]
    )


def lobby_keyboard(
    lang: str,
    game_id: int,
    bot_username: str,
    chat_id: int,
    active: bool = True,
) -> InlineKeyboardMarkup | None:
    if not active:
        return None
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


def go_vote_private_keyboard(settings: Settings, game_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Ovoz berish",
                    url=f"https://t.me/{_clean_bot_username(settings.bot_username)}?start=vote_{game_id}",
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


def go_group_keyboard(chat_id: int, group_url: str | None = None) -> InlineKeyboardMarkup:
    url = group_url or group_url_from_chat_id(chat_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Guruhga qaytish ↗", url=url)],
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
    return InlineKeyboardMarkup(inline_keyboard=rows)


def commissar_action_keyboard(game_id: int, actor_id: int, can_shoot: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="🕵️ Tekshirish - kimning tomonini bilish",
                callback_data=f"commissar:check:{game_id}:{actor_id}",
            )
        ]
    ]
    if can_shoot:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔫 Otish - shubhalini yo'q qilish",
                    callback_data=f"commissar:shoot:{game_id}:{actor_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def vote_keyboard(game_id: int, choices: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=name, callback_data=f"vote:{game_id}:{target_id}")]
            for target_id, name in choices
        ]
    )


def confirm_hang_keyboard(game_id: int, target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👍", callback_data=f"hang:yes:{game_id}:{target_id}"),
                InlineKeyboardButton(text="👎", callback_data=f"hang:no:{game_id}:{target_id}"),
            ]
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
            ]
        ]
    )


def settings_keyboard(lang: str, game_id: int | None = None) -> InlineKeyboardMarkup:
    items = [
        ("settings:lang", "🌍 Til sozlamasi"),
        ("settings:timeout", "⏳ Registration timeout"),
        ("settings:minplayers", "👥 Minimum players"),
        ("settings:roles", "🎭 Role settings"),
        ("settings:premium", "🎲 Premium status"),
        ("settings:logs", "🧾 Game logs"),
        ("settings:media", "🖼 Day/Night media"),
        ("settings:stop", "🛑 Stop game"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=cb)] for cb, label in items]
    )


def roles_overview_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=role_label(r), callback_data="noop") ] for r in Role]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def shop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛡 Himoya - $120", callback_data="shop:buy:protection")],
            [InlineKeyboardButton(text="⛑️ Qotildan himoya - $100", callback_data="shop:buy:killer_protection")],
            [InlineKeyboardButton(text="⚖️ Ovoz himoyasi - $80", callback_data="shop:buy:vote_protection")],
            [InlineKeyboardButton(text="🔫 Miltiq - $150", callback_data="shop:buy:gun")],
            [InlineKeyboardButton(text="🎭 Maska - $70", callback_data="shop:buy:mask")],
            [InlineKeyboardButton(text="📁 Soxta hujjat - $70", callback_data="shop:buy:fake_document")],
            [InlineKeyboardButton(text="🃏 Keyingi rol tanlash", callback_data="shop:roles")],
        ]
    )


def role_shop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🕵🏻‍♂ Komissar - $300", callback_data="shop:role:commissar")],
            [InlineKeyboardButton(text="👨🏻‍⚕ Doktor - $260", callback_data="shop:role:doctor")],
            [InlineKeyboardButton(text="🤵🏻 Don - $500", callback_data="shop:role:don")],
            [InlineKeyboardButton(text="🔪 Qotil - $450", callback_data="shop:role:killer")],
            [InlineKeyboardButton(text="◀️ Orqaga", callback_data="shop:open")],
        ]
    )


def owner_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Statistika", callback_data="owner:stats")],
            [InlineKeyboardButton(text="🎲 Premium guruhlar", callback_data="owner:premium_groups")],
            [InlineKeyboardButton(text="📣 Userlarga reklama", callback_data="owner:broadcast_users")],
            [InlineKeyboardButton(text="🏘 Guruhlarga reklama", callback_data="owner:broadcast_groups")],
            [InlineKeyboardButton(text="🎁 Kredit berish", callback_data="owner:grant_help")],
            [InlineKeyboardButton(text="🧾 Yordam", callback_data="owner:help")],
        ]
    )


def owner_wait_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="owner:cancel")],
        ]
    )


def owner_premium_groups_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Premium guruh qo'shish", callback_data="owner:premium_add")],
            [InlineKeyboardButton(text="📋 Premium guruhlar ro'yxati", callback_data="owner:premium_list")],
            [InlineKeyboardButton(text="◀️ Admin panel", callback_data="owner:panel")],
        ]
    )


def premium_groups_keyboard(groups: list[object]) -> InlineKeyboardMarkup:
    rows = []
    for group in groups:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🎲 {group.title} - 💎 {group.diamond_price}",
                    url=group.invite_link,
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="start:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
