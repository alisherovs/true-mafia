from __future__ import annotations

from typing import Optional
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import Settings
from app.enums import Role
from app.roles import ACTIVE_ROLE_POOL, SHOP_ROLE_CATALOG, role_label
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
            _toggle_button("📁", "use_fake_document", user),
            _toggle_button("⚖️", "use_vote_protection", user),
        ],
        [
            _toggle_button("💊", "use_drug_protection", user),
            _toggle_button("🧿", "use_killer_protection", user),
        ],
        [
            _toggle_button("🎭", "use_mask", user),
            _toggle_button("🛡", "use_protection", user),
        ],
        [InlineKeyboardButton(text="Do'kon", callback_data="shop:open")],
        [
            InlineKeyboardButton(text="💎 Xarid qilish", callback_data="diamond:shop"),
            InlineKeyboardButton(text="💵 Xarid qilish", callback_data="dollar:shop"),
        ],
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
    Role.LOVE_ANGEL,
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
            [InlineKeyboardButton(text="🚫 Faol rolni o'chirish - 100💵", callback_data="shop:disable_roles")],
            [InlineKeyboardButton(text="◀️ Orqaga", callback_data="profile:open")],
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
    rows.append([InlineKeyboardButton(text="◀️ Orqaga", callback_data="shop:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def disable_role_shop_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🚫 {role_label(role)} - 100💵", callback_data=f"shop:disable_role:{role.value}")]
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
            [InlineKeyboardButton(text="💎 Almaz loglari", callback_data="owner:diamond_audit")],
            [InlineKeyboardButton(text="🏠 Admin guruh", callback_data="owner:admin_group")],
            [InlineKeyboardButton(text="🎲 Premium guruhlar", callback_data="owner:premium_groups")],
            [InlineKeyboardButton(text="🚷 Blacklist", callback_data="owner:premium_blocked_list")],
            [InlineKeyboardButton(text="👋 Salomlashuv", callback_data="owner:welcome")],
            [InlineKeyboardButton(text="👤 Xarid admini", callback_data="owner:purchase_admin")],
            [InlineKeyboardButton(text="📰 Yangiliklar kanali", callback_data="owner:news_channel")],
            [InlineKeyboardButton(text="🥷 Geroy savdo kanali", callback_data="owner:hero_market_channel")],
            [InlineKeyboardButton(text="📣 Userlarga reklama", callback_data="owner:broadcast_users")],
            [InlineKeyboardButton(text="🏘 Guruhlarga reklama", callback_data="owner:broadcast_groups")],
            [InlineKeyboardButton(text="🎁 Kredit berish", callback_data="owner:grant_help")],
            [InlineKeyboardButton(text="📋 Barcha buyruqlar", callback_data="owner:commands")],
            [InlineKeyboardButton(text="🧾 Yordam", callback_data="owner:help")],
        ]
    )


def owner_diamond_audit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash", callback_data="owner:diamond_audit")],
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


def owner_welcome_keyboard(enabled: bool, has_media: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="🔴 O'chirish" if enabled else "🟢 Yoqish",
                callback_data="owner:welcome_toggle",
            )
        ],
        [InlineKeyboardButton(text="✏️ Matnni o'zgartirish", callback_data="owner:welcome_text")],
        [InlineKeyboardButton(text="🖼 Media qo'shish / o'zgartirish", callback_data="owner:welcome_media")],
    ]
    if has_media:
        rows.append([InlineKeyboardButton(text="🗑 Mediani o'chirish", callback_data="owner:welcome_media_clear")])
    rows.append([InlineKeyboardButton(text="◀️ Admin panel", callback_data="owner:panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
