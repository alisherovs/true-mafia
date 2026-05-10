from __future__ import annotations

from typing import Optional
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import Settings
from app.enums import Role
from app.roles import ACTIVE_ROLE_POOL, role_label
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
        [InlineKeyboardButton(text="💎 Almaz sotib olish", callback_data="diamond:shop")],
        [
            InlineKeyboardButton(text=t(lang, "lang"), callback_data="lang:menu:user:0"),
            InlineKeyboardButton(text=t(lang, "rules_btn"), callback_data="rules:show"),
        ],
        [InlineKeyboardButton(text="📋 Buyruqlar", callback_data="commands:open")],
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
) -> InlineKeyboardMarkup:
    rows = [
        [
            _toggle_button("📁", "use_fake_document", user),
            _toggle_button("🛡", "use_protection", user),
            _toggle_button("🎭", "use_mask", user),
        ],
        [
            _toggle_button("⛑", "use_killer_protection", user),
            _toggle_button("🔫", "use_gun", user),
            _toggle_button("⚖️", "use_vote_protection", user),
        ],
        [
            _toggle_button("💊", "use_drug_protection", user),
            _toggle_button("📦", "use_miner_protection", user),
        ],
        [InlineKeyboardButton(text="Do'kon", callback_data="shop:open")],
        [
            InlineKeyboardButton(text="Xarid qilish 💵", callback_data="dollar:shop"),
            InlineKeyboardButton(text="Xarid qilish 💎", callback_data="diamond:shop"),
        ],
        [
            InlineKeyboardButton(text="🌍 Til", callback_data="lang:menu:user:0"),
            InlineKeyboardButton(text="🃏 O'yin qoidalari", callback_data="rules:show"),
        ],
        [InlineKeyboardButton(text="🎲 Premium guruhlar", callback_data="premium:info")],
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
) -> Optional[InlineKeyboardMarkup]:
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
        (callback("premium"), "🎲 Premium status"),
        (callback("logs"), "🧾 Game logs"),
        (callback("media"), "🖼 Day/Night media"),
        (callback("stop"), "🛑 Stop game"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=cb)] for cb, label in items]
    )


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


def shop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛡 Himoya - 100💵", callback_data="shop:buy:protection")],
            [InlineKeyboardButton(text="📁 Hujjat - 190💵", callback_data="shop:buy:fake_document")],
            [InlineKeyboardButton(text="⚖️ Ovozdan himoya - 1💎", callback_data="shop:buy:vote_protection")],
            [InlineKeyboardButton(text="🔫 Miltiq - 1💎", callback_data="shop:buy:gun")],
            [InlineKeyboardButton(text="💊 Doridan himoya - 100💵", callback_data="shop:buy:drug_protection")],
            [InlineKeyboardButton(text="🎭 Maska - 100💵", callback_data="shop:buy:mask")],
            [InlineKeyboardButton(text="⛑️ Qotildan himoya - 2💎", callback_data="shop:buy:killer_protection")],
            [InlineKeyboardButton(text="📦 Sirpanishdan himoya - 300💵", callback_data="shop:buy:miner_protection")],
            [InlineKeyboardButton(text="🃏 Keyingi rol tanlash", callback_data="shop:roles")],
            [InlineKeyboardButton(text="🚫 Faol rolni o'chirish - 200💵", callback_data="shop:disable_roles")],
            [InlineKeyboardButton(text="◀️ Orqaga", callback_data="start:back")],
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


def disable_role_shop_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🚫 {role_label(role)} - $200", callback_data=f"shop:disable_role:{role.value}")]
        for role in ACTIVE_ROLE_POOL
        if role not in {Role.CITIZEN, Role.DON}
    ]
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="shop:open")])
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
                InlineKeyboardButton(text="◀️ Orqaga", callback_data="start:back"),
            ],
        ]
    )


def owner_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Statistika", callback_data="owner:stats")],
            [InlineKeyboardButton(text="🎲 Premium guruhlar", callback_data="owner:premium_groups")],
            [InlineKeyboardButton(text="� Blacklist", callback_data="owner:blacklist")],
            [InlineKeyboardButton(text="�👤 Xarid admini", callback_data="owner:purchase_admin")],
            [InlineKeyboardButton(text="📰 Yangiliklar kanali", callback_data="owner:news_channel")],
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
