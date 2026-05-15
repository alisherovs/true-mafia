from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.game_engine import GameEngine
from app.group_settings import GroupSettingsManager
from app.keyboards import (
    group_welcome_keyboard,
    settings_main_keyboard,
    settings_giveaway_keyboard,
    settings_giveaway_amount_keyboard,
    settings_roles_keyboard,
    settings_role_toggle_keyboard,
    settings_weapons_keyboard,
    settings_weapon_toggle_keyboard,
    settings_leave_keyboard,
    settings_permissions_keyboard,
    settings_permission_level_keyboard,
    settings_chat_keyboard,
    settings_chat_phase_keyboard,
    settings_times_keyboard,
    settings_time_value_keyboard,
    settings_mode_keyboard,
    settings_extra_keyboard,
    settings_extra_toggle_keyboard,
    settings_panel_keyboard,
)
from app.texts import t

router = Router()
PENDING_GROUP_WELCOME_ACTIONS: dict[int, dict[str, int | str]] = {}
SETTINGS_CHAT_MAP: dict[int, int] = {}

ROLE_LABELS: dict[str, str] = {
    "don": "🤵🏻 Don", "mafia": "🤵🏼 Mafia", "commissar_katani": "🕵🏼 Komissar Katani",
    "doctor": "👨🏼‍⚕️ Doktor", "sergeant": "👮🏼 Serjant", "gentleman": "🎖 Janob",
    "citizen": "👨🏼 Tinch aholi", "wanderer": "🧙‍♂️ Daydi", "traveler": "💃 Kezuvchi",
    "lawyer": "👨🏼‍💼 Advokat", "suicide": "🤦 Suidsid", "lucky": "🤞 Omadli",
    "wolf": "🐺 Bo'ri", "killer": "🔪 Qotil", "mercenary_killer": "🥷 Yollanma qotil",
    "sorcerer": "💣 Afsungar", "swindler": "🃏 Aferist", "magician": "🧙 Sehrgar",
    "angry": "🧟 G'azabkor", "journalist": "📰 Jurnalist", "traitor": "😎 Sotqin",
    "chemist": "🧪 Kimyogar", "guard": "🛡 Qo'riqchi", "joker": "🃏 Xazilkash",
}

WEAPON_LABELS: dict[str, str] = {
    "protection": "🛡 Himoya", "document": "📁 Hujjat", "killer_protection": "🚨 Qotildan himoya",
    "vote_protection": "⚖️ Ovozdan himoya", "gun": "🔫 Miltiq", "medicine_protection": "💊 Doridan himoya",
    "slip_protection": "📦 Sirpanishdan himoya", "mask": "🎭 Maska", "hero": "🥷 Geroy", "active_role": "🃏 Faol rol",
}

COMMAND_LABELS: dict[str, str] = {
    "start": "start", "stop": "stop", "game": "game", "top_1": "Top 1", "top_7": "Top 7",
    "top_30": "Top 30", "reward_top_1": "Taqdirlash Top 1", "reward_top_7": "Taqdirlash Top 7",
    "reward_top_30": "Taqdirlash Top 30",
}

PERMISSION_LABELS: dict[str, str] = {"owner": "👑 Ega", "admin": "🛡 Admin", "user": "👥 Obunachilar"}

CHAT_PERMISSION_LABELS: dict[str, str] = {
    "owner": "👑 Faqat Ega", "admin": "🛡 Faqat Adminlar",
    "alive_players": "💚 Faqat tirik ishtirokchilar", "players": "🎮 Faqat ishtirokchilar", "all": "👥 Hamma",
}

MODE_LABELS: dict[str, str] = {"normal": "🎲 Oddiy", "fast": "⚡ Tezkor", "protected": "🛡 Himoyali", "hard": "🔥 Qiyin"}

EXTRA_LABELS: dict[str, str] = {
    "notifications": "🔔 Bildirishnoma", "auto_clean": "🗑 Avto tozalash",
    "pin_message": "📌 Pin xabar", "result_announce": "📢 Natija e'loni",
}


def _get_chat_id(callback: CallbackQuery) -> int:
    return SETTINGS_CHAT_MAP.get(callback.from_user.id, 0)


async def _check_admin(bot, chat_id: int, user_id: int, engine: GameEngine) -> bool:
    if chat_id == 0:
        return False
    return await engine.is_admin_or_creator(bot, chat_id, user_id)


async def _ensure_chat_id(callback: CallbackQuery) -> int:
    chat_id = _get_chat_id(callback)
    if chat_id == 0:
        await callback.answer("⚠️ Guruhda /settings qayta yuboring.", show_alert=True)
    return chat_id


async def _deny_if_not_admin(callback: CallbackQuery, chat_id: int, engine: GameEngine) -> bool:
    if chat_id == 0:
        await callback.answer("⚠️ Guruhda /settings qayta yuboring.", show_alert=True)
        return False
    allowed = await _check_admin(callback.bot, chat_id, callback.from_user.id, engine)
    if not allowed:
        await callback.answer("❌ Sizda bu sozlamani o'zgartirish huquqi yo'q.", show_alert=True)
        return False
    return True


@router.message(Command("settings"))
async def cmd_settings(message: Message, engine: GameEngine) -> None:
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "group_only"))
        return
    allowed = await _check_admin(message.bot, message.chat.id, message.from_user.id, engine)
    if not allowed:
        await message.reply("❌ Sizda bu sozlamani o'zgartirish huquqi yo'q.")
        return
    SETTINGS_CHAT_MAP[message.from_user.id] = message.chat.id
    text = "⚙️ <b>Guruh sozlamalari</b>\n\nQuyidagi bo'limlardan birini tanlang:"
    try:
        await message.bot.send_message(message.from_user.id, text, reply_markup=settings_main_keyboard())
        await message.reply("⚙️ Sozlamalar bot private chatiga yuborildi.")
    except TelegramForbiddenError:
        await message.reply("⚠️ Sozlamalarni botda ochish uchun avval botga /start bosing.")


@router.callback_query(F.data == "settings:exit")
async def settings_exit(callback: CallbackQuery) -> None:
    SETTINGS_CHAT_MAP.pop(callback.from_user.id, None)
    if callback.message:
        try:
            await callback.message.edit_text("✅ Sozlamalardan chiqildi.")
        except TelegramBadRequest:
            pass
    await callback.answer()


@router.callback_query(F.data.startswith("settings:back:"))
async def settings_back(callback: CallbackQuery, engine: GameEngine) -> None:
    target = callback.data.split(":", 2)[2]
    if callback.message is None:
        await callback.answer()
        return
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    if target == "main":
        await callback.message.edit_text("⚙️ <b>Guruh sozlamalari</b>\n\nQuyidagi bo'limlardan birini tanlang:", reply_markup=settings_main_keyboard())
    elif target == "giveaway":
        await callback.message.edit_text("🎁 <b>Giveawaylar</b>\n\nQaysi giveaway turini sozlamoqchisiz?", reply_markup=settings_giveaway_keyboard())
    elif target == "roles":
        states = await gsm.get_all_roles(chat_id)
        await callback.message.edit_text("🎭 <b>Rollar</b>\n\nQaysi rolni sozlamoqchisiz?", reply_markup=settings_roles_keyboard(states))
    elif target == "weapons":
        states = await gsm.get_all_weapons(chat_id)
        await callback.message.edit_text("🔫 <b>Qurollar</b>\n\nQaysi qurolni sozlamoqchisiz?", reply_markup=settings_weapons_keyboard(states))
    elif target == "permissions":
        states = await gsm.get_all_command_permissions(chat_id)
        await callback.message.edit_text("🔐 <b>Buyruqlarga ruxsatlar</b>\n\nQaysi buyruqqa ruxsat bermoqchisiz?", reply_markup=settings_permissions_keyboard(states))
    elif target == "chat":
        await callback.message.edit_text("✍️ <b>Yozishni cheklash</b>\n\nQaysi paytni sozlaymiz?", reply_markup=settings_chat_keyboard())
    elif target == "times":
        await callback.message.edit_text("⏰ <b>Vaqtlar</b>\n\nQaysi vaqtni sozlaysiz?", reply_markup=settings_times_keyboard())
    elif target == "extra":
        states = await gsm.get_all_extra(chat_id)
        await callback.message.edit_text("⚙️ <b>Boshqa sozlamalar</b>\n\nKerakli sozlamani tanlang:", reply_markup=settings_extra_keyboard(states))
    await callback.answer()


# ── GIVEAWAY ──

@router.callback_query(F.data == "settings:giveaway")
async def settings_giveaway_menu(callback: CallbackQuery) -> None:
    if callback.message is None: await callback.answer(); return
    await callback.message.edit_text("🎁 <b>Giveawaylar</b>\n\nQaysi giveaway turini sozlamoqchisiz?", reply_markup=settings_giveaway_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("settings:giveaway:diamond"))
async def settings_giveaway_diamond(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    data = callback.data.split(":")
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    if len(data) == 3:
        gs = await gsm.get_settings(chat_id)
        await callback.message.edit_text("💎 <b>Olmoslar</b>\n\nNechta berilsin?", reply_markup=settings_giveaway_amount_keyboard("diamond", gs.giveaway_diamond))
    elif len(data) == 4:
        amount = int(data[3])
        if not await _deny_if_not_admin(callback, chat_id, engine): return
        await gsm.set_giveaway_diamond(chat_id, amount)
        await callback.message.edit_text(f"💎 <b>Olmoslar</b>\n\n✅ Olmos giveaway qiymati {amount} ga o'zgartirildi.", reply_markup=settings_giveaway_amount_keyboard("diamond", amount))
    await callback.answer()


@router.callback_query(F.data.startswith("settings:giveaway:protection"))
async def settings_giveaway_protection(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    data = callback.data.split(":")
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    if len(data) == 3:
        gs = await gsm.get_settings(chat_id)
        await callback.message.edit_text("🛡 <b>Himoyalar</b>\n\nNechta berilsin?", reply_markup=settings_giveaway_amount_keyboard("protection", gs.giveaway_protection))
    elif len(data) == 4:
        amount = int(data[3])
        if not await _deny_if_not_admin(callback, chat_id, engine): return
        await gsm.set_giveaway_protection(chat_id, amount)
        await callback.message.edit_text(f"🛡 <b>Himoyalar</b>\n\n✅ Himoya giveaway qiymati {amount} ga o'zgartirildi.", reply_markup=settings_giveaway_amount_keyboard("protection", amount))
    await callback.answer()


# ── ROLES ──

@router.callback_query(F.data == "settings:roles")
async def settings_roles_menu(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    states = await gsm.get_all_roles(chat_id)
    await callback.message.edit_text("🎭 <b>Rollar</b>\n\nQaysi rolni sozlamoqchisiz?", reply_markup=settings_roles_keyboard(states))
    await callback.answer()


@router.callback_query(F.data.startswith("settings:role:"))
async def settings_role_handler(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    parts = callback.data.split(":")
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    if len(parts) == 3:
        role_key = parts[2]
        label = ROLE_LABELS.get(role_key, role_key)
        is_allowed = await gsm.get_role_allowed(chat_id, role_key)
        await callback.message.edit_text(f"{label} roli taqiqlansinmi?", reply_markup=settings_role_toggle_keyboard(role_key, is_allowed))
    elif len(parts) == 4:
        role_key, action = parts[2], parts[3]
        if not await _deny_if_not_admin(callback, chat_id, engine): return
        label = ROLE_LABELS.get(role_key, role_key)
        if action == "ban":
            await gsm.set_role_allowed(chat_id, role_key, False)
            states = await gsm.get_all_roles(chat_id)
            await callback.message.edit_text(f"🚫 {label} roli ushbu guruhda taqiqlandi.", reply_markup=settings_roles_keyboard(states))
        elif action == "allow":
            await gsm.set_role_allowed(chat_id, role_key, True)
            states = await gsm.get_all_roles(chat_id)
            await callback.message.edit_text(f"✅ {label} roli ushbu guruhda ruxsat berildi.", reply_markup=settings_roles_keyboard(states))
    await callback.answer()


# ── WEAPONS ──

@router.callback_query(F.data == "settings:weapons")
async def settings_weapons_menu(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    states = await gsm.get_all_weapons(chat_id)
    await callback.message.edit_text("🔫 <b>Qurollar</b>\n\nQaysi qurolni sozlamoqchisiz?", reply_markup=settings_weapons_keyboard(states))
    await callback.answer()


@router.callback_query(F.data.startswith("settings:weapon:"))
async def settings_weapon_handler(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    parts = callback.data.split(":")
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    if len(parts) == 3:
        weapon_key = parts[2]
        label = WEAPON_LABELS.get(weapon_key, weapon_key)
        is_enabled = await gsm.get_weapon_enabled(chat_id, weapon_key)
        await callback.message.edit_text(f"{label} yoqilganmi?", reply_markup=settings_weapon_toggle_keyboard(weapon_key, is_enabled))
    elif len(parts) == 4:
        weapon_key, action = parts[2], parts[3]
        if not await _deny_if_not_admin(callback, chat_id, engine): return
        label = WEAPON_LABELS.get(weapon_key, weapon_key)
        if action == "on":
            await gsm.set_weapon_enabled(chat_id, weapon_key, True)
            states = await gsm.get_all_weapons(chat_id)
            await callback.message.edit_text(f"✅ {label} yoqildi.", reply_markup=settings_weapons_keyboard(states))
        elif action == "off":
            await gsm.set_weapon_enabled(chat_id, weapon_key, False)
            states = await gsm.get_all_weapons(chat_id)
            await callback.message.edit_text(f"🚫 {label} o'chirildi.", reply_markup=settings_weapons_keyboard(states))
    await callback.answer()


# ── LEAVE ──

@router.callback_query(F.data == "settings:leave")
async def settings_leave_menu(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    gs = await gsm.get_settings(chat_id)
    await callback.message.edit_text("🚪 <b>/leave buyrug'iga ruxsat beramizmi?</b>", reply_markup=settings_leave_keyboard(gs.leave_allowed))
    await callback.answer()


@router.callback_query(F.data.startswith("settings:leave:"))
async def settings_leave_handler(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    action = callback.data.split(":")[2]
    chat_id = _get_chat_id(callback)
    if not await _deny_if_not_admin(callback, chat_id, engine): return
    gsm = GroupSettingsManager(engine.session_factory)
    if action == "on":
        await gsm.set_leave_allowed(chat_id, True)
        await callback.message.edit_text("✅ /leave buyrug'i yoqildi.", reply_markup=settings_leave_keyboard(True))
    elif action == "off":
        await gsm.set_leave_allowed(chat_id, False)
        await callback.message.edit_text("🚫 /leave buyrug'i o'chirildi.", reply_markup=settings_leave_keyboard(False))
    await callback.answer()


# ── PERMISSIONS ──

@router.callback_query(F.data == "settings:permissions")
async def settings_permissions_menu(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    states = await gsm.get_all_command_permissions(chat_id)
    await callback.message.edit_text("🔐 <b>Buyruqlarga ruxsatlar</b>\n\nQaysi buyruqqa ruxsat bermoqchisiz?", reply_markup=settings_permissions_keyboard(states))
    await callback.answer()


@router.callback_query(F.data.startswith("settings:permission:"))
async def settings_permission_handler(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    parts = callback.data.split(":")
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    if len(parts) == 3:
        cmd_key = parts[2]
        label = COMMAND_LABELS.get(cmd_key, cmd_key)
        current = await gsm.get_command_permission(chat_id, cmd_key)
        await callback.message.edit_text(f"[{label}] buyrug'ini kimlar ishlata oladi?", reply_markup=settings_permission_level_keyboard(cmd_key, current))
    elif len(parts) == 4:
        cmd_key, level = parts[2], parts[3]
        if not await _deny_if_not_admin(callback, chat_id, engine): return
        await gsm.set_command_permission(chat_id, cmd_key, level)
        label = COMMAND_LABELS.get(cmd_key, cmd_key)
        level_label = PERMISSION_LABELS.get(level, level)
        states = await gsm.get_all_command_permissions(chat_id)
        await callback.message.edit_text(f"✅ [{label}] uchun ruxsat: {level_label}", reply_markup=settings_permissions_keyboard(states))
    await callback.answer()


# ── CHAT ──

@router.callback_query(F.data == "settings:chat")
async def settings_chat_menu(callback: CallbackQuery) -> None:
    if callback.message is None: await callback.answer(); return
    await callback.message.edit_text("✍️ <b>Yozishni cheklash</b>\n\nQaysi paytni sozlaymiz?", reply_markup=settings_chat_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("settings:chat:"))
async def settings_chat_handler(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    parts = callback.data.split(":")
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    if len(parts) == 3:
        phase = parts[2]
        phase_label = "🌙 Tun" if phase == "night" else "☀️ Kun"
        current = await gsm.get_chat_permission(chat_id, phase)
        await callback.message.edit_text(f"{phase_label} - Kimlar yozishi mumkin?", reply_markup=settings_chat_phase_keyboard(phase, current))
    elif len(parts) == 4:
        phase, permission = parts[2], parts[3]
        if not await _deny_if_not_admin(callback, chat_id, engine): return
        await gsm.set_chat_permission(chat_id, phase, permission)
        phase_label = "🌙 Tun" if phase == "night" else "☀️ Kun"
        perm_label = CHAT_PERMISSION_LABELS.get(permission, permission)
        await callback.message.edit_text(f"✅ {phase_label}: {perm_label}", reply_markup=settings_chat_phase_keyboard(phase, permission))
    await callback.answer()


# ── TIMES ──

@router.callback_query(F.data == "settings:times")
async def settings_times_menu(callback: CallbackQuery) -> None:
    if callback.message is None: await callback.answer(); return
    await callback.message.edit_text("⏰ <b>Vaqtlar</b>\n\nQaysi vaqtni sozlaysiz?", reply_markup=settings_times_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("settings:time:"))
async def settings_time_handler(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    parts = callback.data.split(":")
    time_labels = {"night_time": "🌙 Tun vaqti", "day_time": "☀️ Kun vaqti", "vote_time": "🗳 Ovoz berish", "registration_time": "⏳ Ro'yxatdan o'tish"}
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    if len(parts) == 3:
        time_key = parts[2]
        label = time_labels.get(time_key, time_key)
        current = await gsm.get_time_setting(chat_id, time_key)
        await callback.message.edit_text(f"{label}\n\nQancha vaqt bo'lsin?", reply_markup=settings_time_value_keyboard(time_key, current))
    elif len(parts) == 4:
        time_key, seconds = parts[2], int(parts[3])
        if not await _deny_if_not_admin(callback, chat_id, engine): return
        await gsm.set_time_setting(chat_id, time_key, seconds)
        label = time_labels.get(time_key, time_key)
        await callback.message.edit_text(f"✅ {label}: {seconds} soniya", reply_markup=settings_time_value_keyboard(time_key, seconds))
    await callback.answer()


# ── MODE ──

@router.callback_query(F.data == "settings:mode")
async def settings_mode_menu(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    gs = await gsm.get_settings(chat_id)
    await callback.message.edit_text("🎮 <b>O'yin modi</b>\n\nQaysi modda o'ynaymiz?", reply_markup=settings_mode_keyboard(gs.game_mode))
    await callback.answer()


@router.callback_query(F.data.startswith("settings:mode:"))
async def settings_mode_handler(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    mode = callback.data.split(":")[2]
    chat_id = _get_chat_id(callback)
    if not await _deny_if_not_admin(callback, chat_id, engine): return
    gsm = GroupSettingsManager(engine.session_factory)
    await gsm.set_game_mode(chat_id, mode)
    label = MODE_LABELS.get(mode, mode)
    await callback.message.edit_text(f"✅ O'yin modi: {label}", reply_markup=settings_mode_keyboard(mode))
    await callback.answer()


# ── EXTRA ──

@router.callback_query(F.data == "settings:extra")
async def settings_extra_menu(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    states = await gsm.get_all_extra(chat_id)
    await callback.message.edit_text("⚙️ <b>Boshqa sozlamalar</b>\n\nKerakli sozlamani tanlang:", reply_markup=settings_extra_keyboard(states))
    await callback.answer()


@router.callback_query(F.data.startswith("settings:extra:"))
async def settings_extra_handler(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    parts = callback.data.split(":")
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    if len(parts) == 3:
        extra_key = parts[2]
        label = EXTRA_LABELS.get(extra_key, extra_key)
        is_enabled = await gsm.get_extra_enabled(chat_id, extra_key)
        await callback.message.edit_text(f"{label} yoqilganmi?", reply_markup=settings_extra_toggle_keyboard(extra_key, is_enabled))
    elif len(parts) == 4:
        extra_key, action = parts[2], parts[3]
        if not await _deny_if_not_admin(callback, chat_id, engine): return
        label = EXTRA_LABELS.get(extra_key, extra_key)
        if action == "on":
            await gsm.set_extra_enabled(chat_id, extra_key, True)
            states = await gsm.get_all_extra(chat_id)
            await callback.message.edit_text(f"✅ {label} yoqildi.", reply_markup=settings_extra_keyboard(states))
        elif action == "off":
            await gsm.set_extra_enabled(chat_id, extra_key, False)
            states = await gsm.get_all_extra(chat_id)
            await callback.message.edit_text(f"🚫 {label} o'chirildi.", reply_markup=settings_extra_keyboard(states))
    await callback.answer()


# ── PANEL ──

@router.callback_query(F.data == "settings:panel")
async def settings_panel(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None: await callback.answer(); return
    chat_id = _get_chat_id(callback)
    gsm = GroupSettingsManager(engine.session_factory)
    data = await gsm.get_panel_data(chat_id)
    leave_status = "✅ Ruxsat" if data["leave_allowed"] else "🚫 Taqiqlangan"
    mode_label = MODE_LABELS.get(data["game_mode"], data["game_mode"])
    cmd_lines = []
    for ck in ["start", "stop", "game"]:
        perm = data["command_permissions"].get(ck, "user")
        cmd_lines.append(f"{COMMAND_LABELS.get(ck, ck)}: {PERMISSION_LABELS.get(perm, perm)}")
    night_label = CHAT_PERMISSION_LABELS.get(data["night_permission"], data["night_permission"])
    day_label = CHAT_PERMISSION_LABELS.get(data["day_permission"], data["day_permission"])
    text = (
        "📊 <b>Boshqaruv paneli</b>\n\n"
        f"🎁 <b>Giveaway:</b>\n💎 Olmos: {data['giveaway_diamond']}\n🛡 Himoya: {data['giveaway_protection']}\n\n"
        f"🎭 <b>Rollar:</b>\n✅ Yoqilgan: {data['roles_enabled']} ta\n🚫 O'chirilgan: {data['roles_disabled']} ta\n\n"
        f"🔫 <b>Qurollar:</b>\n✅ Yoqilgan: {data['weapons_enabled']} ta\n🚫 O'chirilgan: {data['weapons_disabled']} ta\n\n"
        f"🚪 <b>Leave:</b> {leave_status}\n\n"
        "🔐 <b>Buyruqlar:</b>\n" + "\n".join(cmd_lines) + "\n\n"
        f"✍️ <b>Chat:</b>\n🌙 Tun: {night_label}\n☀️ Kun: {day_label}\n\n"
        f"🎮 <b>O'yin modi:</b> {mode_label}"
    )
    await callback.message.edit_text(text, reply_markup=settings_panel_keyboard())
    await callback.answer()


# ── OLD WELCOME HANDLERS (preserved) ──

def _parse_settings_callback(data: str) -> tuple[int | None, str]:
    payload = data.split(":", maxsplit=1)[1]
    if payload.startswith("group:"):
        parts = payload.split(":", maxsplit=2)
        if len(parts) != 3 or not parts[1].lstrip("-").isdigit():
            return None, ""
        return int(parts[1]), parts[2]
    parts = payload.split(":", maxsplit=1)
    if len(parts) != 2 or not parts[0].lstrip("-").isdigit():
        return None, ""
    return int(parts[0]), parts[1]


@router.callback_query(lambda callback: bool(callback.data and callback.data.startswith("settings:") and _parse_settings_callback(callback.data)[1].startswith("welcome")))
async def group_welcome_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None or callback.data is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    target_chat_id, action = _parse_settings_callback(callback.data)
    if target_chat_id is None:
        await callback.answer("Group settings only", show_alert=True)
        return
    allowed = await engine.is_admin_or_creator(callback.bot, target_chat_id, callback.from_user.id)
    if not allowed:
        await callback.answer("Bu inline panel faqat adminlar uchun.", show_alert=True)
        return
    if action == "welcome":
        data = await engine.welcome_settings(target_chat_id)
        await callback.message.edit_text(
            await engine.welcome_settings_text(target_chat_id),
            reply_markup=group_welcome_keyboard(target_chat_id, data["enabled"] == "1", bool(data["media_file_id"])),
        )
        await callback.answer()
        return
    if action == "welcome_toggle":
        enabled, text = await engine.toggle_welcome_enabled(target_chat_id)
        data = await engine.welcome_settings(target_chat_id)
        await callback.message.edit_text(
            await engine.welcome_settings_text(target_chat_id),
            reply_markup=group_welcome_keyboard(target_chat_id, enabled, bool(data["media_file_id"])),
        )
        await callback.answer(text)
        return
    if action == "welcome_text":
        PENDING_GROUP_WELCOME_ACTIONS[callback.from_user.id] = {"action": "text", "chat_id": target_chat_id}
        await callback.message.edit_text(
            "👋 <b>Salomlashuv matni</b>\n\n"
            "User metkasi bot tomonidan avtomatik birinchi qo'yiladi.\n"
            "Siz metkadan keyin chiqadigan matnni yuboring.\n\n"
            "Masalan:\n<code>guruhimizga xush kelibsiz!</code>"
        )
        await callback.answer()
        return
    if action == "welcome_media":
        PENDING_GROUP_WELCOME_ACTIONS[callback.from_user.id] = {"action": "media", "chat_id": target_chat_id}
        await callback.message.edit_text(
            "🖼 <b>Salomlashuv mediasi</b>\n\n"
            "Photo, video, gif yoki document yuboring. Keyingi yangi user kirganda shu media ustiga caption bo'lib salomlashuv chiqadi."
        )
        await callback.answer()
        return
    if action == "welcome_media_clear":
        text = await engine.clear_welcome_media(target_chat_id)
        data = await engine.welcome_settings(target_chat_id)
        await callback.message.edit_text(
            f"{text}\n\n{await engine.welcome_settings_text(target_chat_id)}",
            reply_markup=group_welcome_keyboard(target_chat_id, data["enabled"] == "1", False),
        )
        await callback.answer("O'chirildi.")
        return
    await callback.answer("Unknown action", show_alert=True)


@router.message(lambda message: bool(message.from_user and message.from_user.id in PENDING_GROUP_WELCOME_ACTIONS))
async def handle_group_welcome_pending(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    pending = PENDING_GROUP_WELCOME_ACTIONS.pop(message.from_user.id, None)
    if pending is None:
        return
    target_chat_id = int(pending["chat_id"])
    allowed = await engine.is_admin_or_creator(message.bot, target_chat_id, message.from_user.id)
    if not allowed:
        await message.answer("Bu sozlamani faqat guruh admini o'zgartira oladi.")
        return
    if pending["action"] == "text":
        ok, text = await engine.set_welcome_text(target_chat_id, message.text or "")
        if not ok:
            PENDING_GROUP_WELCOME_ACTIONS[message.from_user.id] = pending
            await message.answer(text)
            return
        data = await engine.welcome_settings(target_chat_id)
        await message.answer(
            f"{text}\n\n{await engine.welcome_settings_text(target_chat_id)}",
            reply_markup=group_welcome_keyboard(target_chat_id, data["enabled"] == "1", bool(data["media_file_id"])),
        )
        return
    media_type = ""
    file_id = ""
    if message.photo:
        media_type = "photo"
        file_id = message.photo[-1].file_id
    elif message.video:
        media_type = "video"
        file_id = message.video.file_id
    elif message.animation:
        media_type = "animation"
        file_id = message.animation.file_id
    elif message.document:
        media_type = "document"
        file_id = message.document.file_id
    ok, text = await engine.set_welcome_media(target_chat_id, media_type, file_id)
    if not ok:
        PENDING_GROUP_WELCOME_ACTIONS[message.from_user.id] = pending
        await message.answer(text)
        return
    data = await engine.welcome_settings(target_chat_id)
    await message.answer(
        f"{text}\n\n{await engine.welcome_settings_text(target_chat_id)}",
        reply_markup=group_welcome_keyboard(target_chat_id, data["enabled"] == "1", bool(data["media_file_id"])),
    )
