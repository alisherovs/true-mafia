from __future__ import annotations

from html import escape
from typing import Union
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message

from app.config import Settings
from app.game_engine import GameEngine
from app.keyboards import (
    owner_admin_group_keyboard,
    owner_channel_gift_mode_keyboard,
    owner_channel_gifts_keyboard,
    owner_diamond_audit_keyboard,
    owner_diamond_top_keyboard,
    owner_dollar_top_keyboard,
    owner_hero_market_keyboard,
    owner_invoice_after_keyboard,
    owner_invoice_delivery_keyboard,
    owner_invoice_menu_keyboard,
    owner_news_channel_keyboard,
    owner_panel_keyboard,
    owner_premium_groups_keyboard,
    owner_wait_keyboard,
)

router = Router()
PENDING_OWNER_ACTIONS: dict[int, str] = {}
PENDING_PREMIUM_GROUPS: dict[int, dict[str, Union[str, int]]] = {}
PENDING_INVOICE_DATA: dict[int, dict[str, Union[str, int]]] = {}
PENDING_CHANNEL_GIFTS: dict[int, dict[str, Union[str, int]]] = {}


def _invoice_summary(data: dict) -> str:
    diamonds = int(data.get("diamonds", 0))
    stars = int(data.get("stars", 0))
    return (
        "🧾 <b>Almaz invoice</b>\n\n"
        f"<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> Almaz: <b>{diamonds}</b>\n"
        f"⭐ Telegram Stars: <b>{stars}</b>\n\n"
        "Yetkazib berish usulini tanlang:"
    )


def _is_owner(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


def _owner_panel_text(stats: str) -> str:
    return (
        "🛡 <b>Owner admin panel</b>\n\n"
        f"{stats}\n\n"
        "Barcha admin amallar tugmalar orqali ishlaydi. "
        "Premium guruh qo'shish, reklama va kredit amallari panel ichidan boshqariladi."
    )


OWNER_COMMANDS_TEXT = (
    "📋 <b>Barcha buyruqlar</b>\n\n"
    "👤 <b>User/private buyruqlar</b>\n"
    "/start - asosiy menyuni ochish\n"
    "/profile - profil va balans ma'lumotlari\n"
    "/commands - user buyruqlari ro'yxati\n"
    "/roles - rollar haqida ma'lumot\n"
    "/lang - tilni o'zgartirish\n"
    "/top - TOP reyting\n\n"
    "🎮 <b>Guruh va o'yin buyruqlari</b>\n"
    "/game - ro'yxatdan o'tishni boshlash yoki +30 sekund uzaytirish\n"
    "/turnir - turnir ro'yxatdan o'tishini boshlash\n"
    "/classic - Classic mode bilan ro'yxatdan o'tishni boshlash\n"
    "/super - Super mode bilan ro'yxatdan o'tishni boshlash\n"
    "/mega - Mega mode bilan ro'yxatdan o'tishni boshlash\n"
    "/start - ro'yxatdan o'tish tugagach o'yinni boshlash\n"
    "/leave - o'yindan chiqish\n"
    "/extend - ro'yxatdan o'tish vaqtini uzaytirish\n"
    "/stop - aktiv o'yinni to'xtatish\n"
    "/settings - guruh sozlamalarini bot private chatida ochish\n"
    "/settimeout soniya - ro'yxatdan o'tish vaqtini sozlash\n"
    "/teamgame - turnir o'yini bo'limi\n"
    "/lastwords matn - o'lim oldi so'zini yozish\n\n"
    "💰 <b>Iqtisod buyruqlari</b>\n"
    "/give miqdor - guruhda sovg'a paneli ochish\n"
    "/give miqdor izoh - reply qilingan userga almaz berish\n"
    "/give user_id miqdor izoh - userga almaz berish\n"
    "/gsend miqdor - guruhni premium reytingga chiqarish uchun almaz yuborish\n\n"
    "🛡 <b>Admin/owner buyruqlari</b>\n"
    "/admin - owner admin panelini ochish\n"
    "/you user_id - user profilidagi balans va itemlarni ko'rish\n"
    "/bust1 user_id izoh - player olmoslarini 0 qilish\n"
    "/bust2 user_id izoh - player dollarlarini 0 qilish\n"
    "/gbust - bot admin bo'lgan guruhni premium ro'yxatdan bankrot qilish\n\n"
    "🧩 <b>Admin panel tugmalari</b>\n"
    "📊 Statistika - bot statistikasi\n"
    "💎 TOP 30 almaz - eng ko'p almazga ega userlar ro'yxati\n"
    "💎 Almaz loglari - kim qancha oldi/sarfladi va nimalarga ketganini ko'rsatadi\n"
    "🏠 Admin guruh - almaz loglari avtomatik yuboriladigan guruhni ulash\n"
    "🎲 Premium guruhlar - premium guruhlarni boshqarish\n"
    "🚷 Blacklist - bloklangan foydalanuvchilar bo'limi\n"
    "Xarid admini - almaz xaridi uchun admin username sozlash\n"
    "Yangiliklar kanali - user paneldagi yangiliklar tugmasini boshqarish\n"
    "Geroy savdo kanali - geroy marketplace kanalini ulash\n"
    "Kanal sovg'a balansi - kanal uchun /send va /change balansini boshqarish\n"
    "Userlarga reklama - barcha userlarga xabar yuborish\n"
    "Guruhlarga reklama - barcha guruhlarga xabar yuborish\n"
    "Kredit berish - userga balans berish yordam oynasi\n"
    "Barcha buyruqlar - mana shu to'liq ro'yxat"
)


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


@router.message(Command("admin"))
async def cmd_owner_admin(message: Message, engine: GameEngine, settings: Settings) -> None:
    if message.from_user is None or not _is_owner(message.from_user.id, settings):
        return
    stats = await engine.owner_stats()
    await message.answer(_owner_panel_text(stats), reply_markup=owner_panel_keyboard())


@router.callback_query(F.data == "owner:panel")
async def owner_panel_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS.pop(callback.from_user.id, None)
    stats = await engine.owner_stats()
    await _safe_edit(callback, _owner_panel_text(stats), reply_markup=owner_panel_keyboard())
    await callback.answer()


@router.callback_query(F.data == "owner:stats")
async def owner_stats_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS.pop(callback.from_user.id, None)
    await _safe_edit(callback, await engine.owner_stats(), reply_markup=owner_panel_keyboard())
    await callback.answer()


@router.callback_query(F.data == "owner:diamond_audit")
async def owner_diamond_audit_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS.pop(callback.from_user.id, None)
    await _safe_edit(callback, await engine.owner_diamond_audit_text(limit=10), reply_markup=owner_diamond_audit_keyboard())
    await callback.answer()


@router.callback_query(F.data == "owner:diamond_top")
async def owner_diamond_top_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS.pop(callback.from_user.id, None)
    await _safe_edit(callback, await engine.owner_diamond_top_text(limit=30), reply_markup=owner_diamond_top_keyboard())
    await callback.answer()


@router.callback_query(F.data == "owner:dollar_top")
async def owner_dollar_top_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS.pop(callback.from_user.id, None)
    await _safe_edit(callback, await engine.owner_dollar_top_text(limit=30), reply_markup=owner_dollar_top_keyboard())
    await callback.answer()


@router.callback_query(F.data == "owner:admin_group")
async def owner_admin_group_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS.pop(callback.from_user.id, None)
    group_id = await engine.get_admin_group_id()
    current = f"<code>{group_id}</code>" if group_id else "<b>ulanmagan</b>"
    can_use_current = callback.message is not None and callback.message.chat.type != "private"
    text = (
        "🏠 <b>Admin guruh</b>\n\n"
        f"Joriy guruh: {current}\n\n"
        "Almaz loglari shu guruhga avtomatik yuboriladi. "
        "Bot ulangan guruhda bo'lishi va xabar yubora olishi kerak."
    )
    await _safe_edit(callback, text, reply_markup=owner_admin_group_keyboard(bool(group_id), can_use_current))
    await callback.answer()


@router.callback_query(F.data == "owner:admin_group:current")
async def owner_admin_group_current_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    if callback.message is None or callback.message.chat.type == "private":
        await callback.answer("Bu tugmani ulanishi kerak bo'lgan guruhda bosing.", show_alert=True)
        return
    ok, text = await engine.set_admin_group(callback.bot, callback.message.chat.id)
    await _safe_edit(callback, text, reply_markup=owner_admin_group_keyboard(ok, True))
    await callback.answer("Ulandi." if ok else "Xato", show_alert=not ok)


@router.callback_query(F.data == "owner:admin_group:set")
async def owner_admin_group_set_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "admin_group_id"
    await _safe_edit(
        callback,
        "🏠 <b>Admin guruhni ulash</b>\n\n"
        "Guruh ID yuboring. Masalan:\n<code>-1001234567890</code>\n\n"
        "Yoki /admin buyrug'ini kerakli guruhda ochib, <b>Shu guruhni ulash</b> tugmasini bosing.",
        reply_markup=owner_wait_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:admin_group:clear")
async def owner_admin_group_clear_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    text = await engine.clear_admin_group()
    await _safe_edit(callback, text, reply_markup=owner_admin_group_keyboard(False, callback.message is not None and callback.message.chat.type != "private"))
    await callback.answer("O'chirildi.")


@router.callback_query(F.data == "owner:commands")
async def owner_commands_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS.pop(callback.from_user.id, None)
    await _safe_edit(callback, OWNER_COMMANDS_TEXT, reply_markup=owner_panel_keyboard())
    await callback.answer()


@router.callback_query(F.data == "owner:premium_groups")
async def owner_premium_groups_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    groups = await engine.premium_groups(include_inactive=True)
    await _safe_edit(callback, await engine.owner_premium_groups_manage_text(), reply_markup=owner_premium_groups_keyboard(groups))
    await callback.answer()


@router.callback_query(F.data == "owner:premium_add")
async def owner_premium_add_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "premium_title"
    PENDING_PREMIUM_GROUPS[callback.from_user.id] = {}
    if callback.message:
        await callback.message.edit_text(
            "➕ <b>Premium guruh qo'shish</b>\n\n"
            "1/3. Premium guruh nomini yuboring.\n\n"
            "Masalan: <code>Mafia VIP Club</code>",
            reply_markup=owner_wait_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "owner:premium_list")
async def owner_premium_list_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    groups = await engine.premium_groups(include_inactive=True)
    await _safe_edit(callback, await engine.owner_premium_groups_manage_text(), reply_markup=owner_premium_groups_keyboard(groups))
    await callback.answer()


@router.callback_query(F.data == "owner:premium_timer")
async def owner_premium_timer_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    current = await engine.premium_reset_timer_text()
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "premium_timer"
    await _safe_edit(
        callback,
        "⏱ <b>Premium guruhlar timeri</b>\n\n"
        f"{current}\n\n"
        "Necha daqiqadan keyin premium guruh balansi 0 bo'lishini yuboring.\n"
        "Masalan:\n"
        "<code>60</code> - 1 soat\n"
        "<code>1440</code> - 1 kun\n"
        "<code>0</code> - timer o'chiriladi",
        reply_markup=owner_wait_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("owner:premium_bankrupt:"))
async def owner_premium_bankrupt_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    group_id = callback.data.rsplit(":", maxsplit=1)[-1]
    ok, text = await engine.bankrupt_premium_group(group_id)
    groups = await engine.premium_groups(include_inactive=True)
    await _safe_edit(
        callback,
        f"{text}\n\n{await engine.owner_premium_groups_manage_text()}",
        reply_markup=owner_premium_groups_keyboard(groups),
    )
    await callback.answer("Bankrot qilindi." if ok else text, show_alert=not ok)


@router.callback_query(F.data == "owner:premium_block_user")
async def owner_premium_block_user_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "premium_block_user"
    await _safe_edit(
        callback,
        "🚫 <b>Premium user bloklash</b>\n\n"
        "Telegram ID, @username yoki username yuboring. Sabab yozish ixtiyoriy.\n\n"
        "Masalan:\n<code>123456789 reklama</code>",
        reply_markup=owner_wait_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:premium_unblock_user")
async def owner_premium_unblock_user_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "premium_unblock_user"
    await _safe_edit(
        callback,
        "✅ <b>Premium userni blokdan chiqarish</b>\n\n"
        "Telegram ID, @username yoki username yuboring.\n\n"
        "Masalan:\n<code>123456789</code>",
        reply_markup=owner_wait_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:premium_blocked_list")
async def owner_premium_blocked_list_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    groups = await engine.premium_groups(include_inactive=True)
    await _safe_edit(callback, await engine.premium_blocked_users_text(), reply_markup=owner_premium_groups_keyboard(groups))
    await callback.answer()


@router.callback_query(F.data == "owner:purchase_admin")
async def owner_purchase_admin_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    current = await engine.get_purchase_admin_username()
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "purchase_admin_username"
    if callback.message:
        await callback.message.edit_text(
            "👤 <b>Xarid admini</b>\n\n"
            f"Joriy admin: @{current}\n\n"
            "Almaz xaridida <b>Admin orqali</b> tugmasi ochadigan username yuboring.\n"
            "Masalan: <code>@username</code>",
            reply_markup=owner_wait_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "owner:news_channel")
async def owner_news_channel_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    current = await engine.get_news_channel_url()
    current_text = current or "<b>o'chirilgan</b>"
    text = (
        "📰 <b>Yangiliklar kanali</b>\n\n"
        f"Joriy link: {current_text}\n\n"
        "Link qo'shilsa user panel va start menyuda <b>Yangiliklar</b> tugmasi chiqadi. "
        "O'chirilsa tugma ko'rinmaydi."
    )
    await _safe_edit(callback, text, reply_markup=owner_news_channel_keyboard(bool(current)))
    await callback.answer()


@router.callback_query(F.data == "owner:news_set")
async def owner_news_set_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "news_channel_url"
    await _safe_edit(
        callback,
        "📰 <b>Yangiliklar kanalini sozlash</b>\n\n"
        "Kanal yoki guruh linkini yuboring.\n\n"
        "Masalan:\n<code>@kanal</code>\n<code>https://t.me/kanal</code>",
        reply_markup=owner_wait_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:news_clear")
async def owner_news_clear_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    text = await engine.clear_news_channel_url()
    await _safe_edit(callback, text, reply_markup=owner_news_channel_keyboard(False))
    await callback.answer("O'chirildi.")


@router.callback_query(F.data == "owner:hero_market_channel")
async def owner_hero_market_channel_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    current = await engine.get_hero_market_channel_id()
    current_text = f"<code>{current}</code>" if current else "<b>ulanmagan</b>"
    await _safe_edit(
        callback,
        "🥷 <b>Geroy savdo kanali</b>\n\n"
        f"Joriy kanal: {current_text}\n\n"
        "Kanal ID yoki @username yuborib ulang. Bot kanalda admin bo'lishi kerak.",
        reply_markup=owner_hero_market_keyboard(bool(current)),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:channel_gifts")
async def owner_channel_gifts_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS.pop(callback.from_user.id, None)
    await _safe_edit(
        callback,
        "📺 <b>Kanal sovg'a balansi</b>\n\n"
        "Bu bo'lim orqali bot kanalga o'zi almaz tarqatish postini yuboradi.\n"
        "Kanalda /send yoki /change yozish shart emas.\n\n"
        "• Almaz tarqatishni boshlash\n"
        "• Balansni ko'rish\n"
        "• Dollar/olmos bilan to'ldirish",
        reply_markup=owner_channel_gifts_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:channel_gifts:view")
async def owner_channel_gifts_view_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "channel_gifts_view"
    await _safe_edit(
        callback,
        "📊 <b>Kanal balansini ko'rish</b>\n\n"
        "Kanal ID yuboring.\n"
        "Masalan: <code>-1001234567890</code>",
        reply_markup=owner_wait_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:channel_gifts:grant")
async def owner_channel_gifts_grant_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "channel_gifts_grant"
    await _safe_edit(
        callback,
        "➕ <b>Kanal balansini to'ldirish</b>\n\n"
        "Quyidagi formatda yuboring:\n"
        "<code>kanal_id dollar diamond</code>\n\n"
        "Masalan:\n"
        "<code>-1001234567890 0 500</code>",
        reply_markup=owner_wait_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:channel_gifts:start")
async def owner_channel_gifts_start_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "channel_gifts_start"
    await _safe_edit(
        callback,
        "▶️ <b>Almaz tarqatishni boshlash</b>\n\n"
        "Kanal ID yuboring. Bot ushbu kanalda admin bo'lishi shart.\n\n"
        "Masalan: <code>-1001234567890</code>",
        reply_markup=owner_wait_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("owner:channel_gifts:mode:"))
async def owner_channel_gifts_mode_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    data = PENDING_CHANNEL_GIFTS.get(callback.from_user.id)
    if not data or "channel_id" not in data:
        await callback.answer("Avval kanal ID kiriting.", show_alert=True)
        PENDING_OWNER_ACTIONS[callback.from_user.id] = "channel_gifts_start"
        await _safe_edit(
            callback,
            "▶️ <b>Almaz tarqatishni boshlash</b>\n\n"
            "Kanal ID yuboring. Masalan: <code>-1001234567890</code>",
            reply_markup=owner_wait_keyboard(),
        )
        return
    mode = callback.data.rsplit(":", maxsplit=1)[-1]
    if mode not in {"send", "change"}:
        await callback.answer("Tur noto'g'ri.", show_alert=True)
        return
    data["mode"] = mode
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "channel_gifts_amount"
    mode_label = "tez tarqatish" if mode == "send" else "ro'yxatdan o'tish"
    minimum = 1 if mode == "send" else 2
    await _safe_edit(
        callback,
        "💎 <b>Miqdorni kiriting</b>\n\n"
        f"Tanlangan tur: <b>{mode_label}</b>\n"
        f"Minimal miqdor: <b>{minimum}</b>\n\n"
        "Nechta olmos tarqatilishini yuboring.\n"
        "Masalan: <code>100</code>",
        reply_markup=owner_wait_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:hero_market_set")
async def owner_hero_market_set_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "hero_market_channel"
    await _safe_edit(
        callback,
        "🥷 <b>Geroy savdo kanalini sozlash</b>\n\n"
        "Kanal ID yoki @username yuboring.\n\n"
        "Masalan:\n<code>@hero_market</code>\n<code>-1001234567890</code>",
        reply_markup=owner_wait_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:hero_market_clear")
async def owner_hero_market_clear_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    text = await engine.clear_hero_market_channel()
    await _safe_edit(callback, text, reply_markup=owner_hero_market_keyboard(False))
    await callback.answer("O'chirildi.")


@router.callback_query(F.data == "owner:broadcast_users")
async def owner_broadcast_users_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "broadcast_users"
    if callback.message:
        await callback.message.edit_text(
            "📣 <b>Userlarga reklama yuborish</b>\n\n"
            "Endi yubormoqchi bo'lgan oddiy xabaringizni jo'nating. "
            "Bot shu xabarni barcha userlarga tarqatadi.",
            reply_markup=owner_wait_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "owner:broadcast_groups")
async def owner_broadcast_groups_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "broadcast_groups"
    if callback.message:
        await callback.message.edit_text(
            "🏘 <b>Guruhlarga reklama yuborish</b>\n\n"
            "Endi yubormoqchi bo'lgan oddiy xabaringizni jo'nating. "
            "Bot shu xabarni barcha guruhlarga tarqatadi.",
            reply_markup=owner_wait_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "owner:grant_help")
async def owner_grant_help_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "grant"
    if callback.message:
        await callback.message.edit_text(
            "🎁 <b>Kredit berish</b>\n\n"
            "Keyingi xabarda shunday yozing:\n"
            "<code>telegram_id dollar diamond</code>\n\n"
            "Masalan:\n"
            "<code>7044905076 500 10</code>",
            reply_markup=owner_wait_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "owner:help")
async def owner_help_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_text(
            "🧾 <b>Admin panel yordam</b>\n\n"
            "📊 Statistika - bot raqamlarini ko'rsatadi.\n"
            "💎 Almaz loglari - almaz kirim-chiqimi, sarf sabablari va oxirgi amallarni ko'rsatadi.\n"
            "🏠 Admin guruh - almaz loglari avtomatik yuboriladigan guruhni ulaydi.\n"
            "🎲 Premium guruhlar - nom, link va olmos narxi bilan premium guruh ulaydi.\n"
            "⏱ Premium timer - premium guruh balansini avtomatik 0 qilish vaqtini sozlaydi.\n"
            "🚷 Blacklist - premium user bloklash va blokdan chiqarish.\n"
            " Xarid admini - almaz xaridi uchun admin username sozlaydi.\n"
            "📰 Yangiliklar kanali - user paneldagi yangiliklar tugmasini boshqaradi.\n"
            "📺 Kanal sovg'a balansi - kanal uchun /send va /change balansini boshqaradi.\n"
            "🥷 Geroy savdo kanali - geroy marketplace kanalini boshqaradi.\n"
            "📣 Userlarga reklama - keyingi oddiy xabarni userlarga tarqatadi.\n"
            "🏘 Guruhlarga reklama - keyingi oddiy xabarni guruhlarga tarqatadi.\n"
            "🎁 Kredit berish - user balansiga dollar/olmos qo'shadi.\n\n"
            "Slash broadcast buyruqlari ishlatilmaydi, hammasi tugma orqali.",
            reply_markup=owner_panel_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "owner:invoice")
async def owner_invoice_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS.pop(callback.from_user.id, None)
    PENDING_INVOICE_DATA.pop(callback.from_user.id, None)
    await _safe_edit(
        callback,
        "🧾 <b>Almaz invoice</b>\n\n"
        "Telegram Stars orqali almaz sotish uchun invoice yarating.",
        reply_markup=owner_invoice_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:invoice:new")
async def owner_invoice_new_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "invoice_amount"
    PENDING_INVOICE_DATA.pop(callback.from_user.id, None)
    await _safe_edit(
        callback,
        "🧾 <b>Yangi almaz invoice</b>\n\n"
        "Almaz miqdori va Telegram Stars narxini yuboring.\n\n"
        "Format:\n<code>almaz stars</code>\n\n"
        "Masalan:\n<code>100 500</code>",
        reply_markup=owner_wait_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:invoice:make_link")
async def owner_invoice_make_link_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    data = PENDING_INVOICE_DATA.get(callback.from_user.id)
    if not data:
        await callback.answer("Invoice ma'lumotlari topilmadi. Qaytadan yarating.", show_alert=True)
        return
    diamonds = int(data.get("diamonds", 0))
    stars = int(data.get("stars", 0))
    try:
        link = await callback.bot.create_invoice_link(
            title=f"💎 {diamonds} almaz",
            description=f"{diamonds} ta almaz sotib olish",
            payload=f"diamonds:{diamonds}:0",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=f"💎 {diamonds} almaz", amount=stars)],
        )
    except Exception as exc:
        await callback.answer(f"Invoice link yaratilmadi: {exc}", show_alert=True)
        return
    PENDING_INVOICE_DATA.pop(callback.from_user.id, None)
    await _safe_edit(
        callback,
        "✅ <b>Invoice link yaratildi</b>\n\n"
        f"{_invoice_summary({'diamonds': diamonds, 'stars': stars})}\n\n"
        f"🔗 {link}",
        reply_markup=owner_invoice_after_keyboard(),
    )
    await callback.answer("Tayyor.")


@router.callback_query(F.data == "owner:invoice:make_send")
async def owner_invoice_make_send_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    if callback.from_user.id not in PENDING_INVOICE_DATA:
        await callback.answer("Invoice ma'lumotlari topilmadi. Qaytadan yarating.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS[callback.from_user.id] = "invoice_user"
    await _safe_edit(
        callback,
        "📤 <b>Invoice yuborish</b>\n\n"
        "Invoice yuboriladigan user Telegram ID sini yuboring.\n\n"
        "Masalan:\n<code>123456789</code>",
        reply_markup=owner_wait_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "owner:cancel")
async def owner_cancel_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS.pop(callback.from_user.id, None)
    PENDING_PREMIUM_GROUPS.pop(callback.from_user.id, None)
    PENDING_INVOICE_DATA.pop(callback.from_user.id, None)
    PENDING_CHANNEL_GIFTS.pop(callback.from_user.id, None)
    if callback.message:
        await callback.message.edit_text(_owner_panel_text(await engine.owner_stats()), reply_markup=owner_panel_keyboard())
    await callback.answer("Bekor qilindi.")


async def _handle_pending_owner_message(message: Message, engine: GameEngine, settings: Settings) -> bool:
    if message.from_user is None or not _is_owner(message.from_user.id, settings):
        return False
    action = PENDING_OWNER_ACTIONS.pop(message.from_user.id, None)
    if action is None:
        return False

    if action == "invoice_amount":
        parts = (message.text or "").split()
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            PENDING_OWNER_ACTIONS[message.from_user.id] = "invoice_amount"
            await message.answer(
                "Format noto'g'ri. Shunday yuboring:\n<code>almaz stars</code>\n\nMasalan: <code>100 500</code>",
                reply_markup=owner_wait_keyboard(),
            )
            return True
        diamonds = int(parts[0])
        stars = int(parts[1])
        if diamonds <= 0 or stars <= 0:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "invoice_amount"
            await message.answer("Almaz va Stars miqdori musbat bo'lishi kerak.", reply_markup=owner_wait_keyboard())
            return True
        PENDING_INVOICE_DATA[message.from_user.id] = {"diamonds": diamonds, "stars": stars}
        await message.answer(_invoice_summary(PENDING_INVOICE_DATA[message.from_user.id]), reply_markup=owner_invoice_delivery_keyboard())
        return True

    if action == "invoice_user":
        data = PENDING_INVOICE_DATA.get(message.from_user.id)
        raw_user_id = (message.text or "").strip()
        if not data or not raw_user_id.isdigit():
            PENDING_OWNER_ACTIONS[message.from_user.id] = "invoice_user"
            await message.answer("User ID noto'g'ri. Faqat raqam yuboring.", reply_markup=owner_wait_keyboard())
            return True
        target_user_id = int(raw_user_id)
        diamonds = int(data.get("diamonds", 0))
        stars = int(data.get("stars", 0))
        try:
            await message.bot.send_invoice(
                chat_id=target_user_id,
                title=f"💎 {diamonds} almaz",
                description=f"{diamonds} ta almaz sotib olish",
                payload=f"diamonds:{diamonds}:{target_user_id}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label=f"💎 {diamonds} almaz", amount=stars)],
            )
        except Exception as exc:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "invoice_user"
            await message.answer(f"Invoice yuborilmadi: {exc}", reply_markup=owner_wait_keyboard())
            return True
        PENDING_INVOICE_DATA.pop(message.from_user.id, None)
        await message.answer(
            "✅ Invoice yuborildi.\n\n"
            f"User ID: <code>{target_user_id}</code>\n"
            f"<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> Almaz: <b>{diamonds}</b>\n"
            f"⭐ Stars: <b>{stars}</b>",
            reply_markup=owner_invoice_after_keyboard(),
        )
        return True

    if action == "premium_title":
        title = (message.text or "").strip()
        if len(title) < 2:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "premium_title"
            await message.answer("Nom juda qisqa. Premium guruh nomini qayta yuboring.", reply_markup=owner_wait_keyboard())
            return True
        PENDING_PREMIUM_GROUPS[message.from_user.id] = {"title": title}
        PENDING_OWNER_ACTIONS[message.from_user.id] = "premium_link"
        await message.answer(
            "2/3. Endi guruh linkini yuboring.\n\n"
            "Masalan: <code>https://t.me/+invite</code> yoki <code>https://t.me/group_username</code>",
            reply_markup=owner_wait_keyboard(),
        )
        return True

    if action == "premium_link":
        link = (message.text or "").strip()
        if not (link.startswith("https://t.me/") or link.startswith("http://t.me/") or link.startswith("t.me/")):
            PENDING_OWNER_ACTIONS[message.from_user.id] = "premium_link"
            await message.answer("Link noto'g'ri. Telegram guruh linkini qayta yuboring.", reply_markup=owner_wait_keyboard())
            return True
        data = PENDING_PREMIUM_GROUPS.setdefault(message.from_user.id, {})
        data["invite_link"] = link
        PENDING_OWNER_ACTIONS[message.from_user.id] = "premium_price"
        await message.answer(
            "3/3. Guruhga qo'shilish narxini olmosda yuboring.\n\n"
            "Masalan: <code>25</code>",
            reply_markup=owner_wait_keyboard(),
        )
        return True

    if action == "premium_price":
        raw_price = (message.text or "").strip()
        if not raw_price.isdigit():
            PENDING_OWNER_ACTIONS[message.from_user.id] = "premium_price"
            await message.answer("Narx faqat son bo'lishi kerak. Masalan: <code>25</code>", reply_markup=owner_wait_keyboard())
            return True
        data = PENDING_PREMIUM_GROUPS.pop(message.from_user.id, {})
        title = str(data.get("title") or "").strip()
        link = str(data.get("invite_link") or "").strip()
        if not title or not link:
            await message.answer("Ma'lumotlar to'liq emas. Qaytadan boshlang.", reply_markup=owner_premium_groups_keyboard())
            return True
        group = await engine.add_premium_group(
            title=title,
            invite_link=link,
            diamond_price=int(raw_price),
            created_by=message.from_user.id,
        )
        await message.answer(
            "✅ Premium guruh qo'shildi.\n\n"
            f"🎲 Nomi: <b>{group.title}</b>\n"
            f"🔗 Link: {group.invite_link}\n"
            f"<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> Narx: <b>{group.diamond_price}</b>",
            reply_markup=owner_premium_groups_keyboard(),
        )
        return True

    if action == "purchase_admin_username":
        ok, text = await engine.set_purchase_admin_username(message.text or "")
        if not ok:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "purchase_admin_username"
            await message.answer(text, reply_markup=owner_wait_keyboard())
            return True
        await message.answer(text, reply_markup=owner_panel_keyboard())
        return True

    if action == "news_channel_url":
        ok, text = await engine.set_news_channel_url(message.text or "")
        if not ok:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "news_channel_url"
            await message.answer(text, reply_markup=owner_wait_keyboard())
            return True
        await message.answer(text, reply_markup=owner_news_channel_keyboard(True))
        return True

    if action == "admin_group_id":
        ok, text = await engine.set_admin_group(message.bot, message.text or "")
        if not ok:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "admin_group_id"
            await message.answer(text, reply_markup=owner_wait_keyboard())
            return True
        await message.answer(text, reply_markup=owner_admin_group_keyboard(True, message.chat.type != "private"))
        return True

    if action == "hero_market_channel":
        ok, text = await engine.set_hero_market_channel(message.bot, message.text or "")
        if not ok:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "hero_market_channel"
            await message.answer(text, reply_markup=owner_wait_keyboard())
            return True
        await message.answer(text, reply_markup=owner_hero_market_keyboard(True))
        return True

    if action == "channel_gifts_view":
        raw = (message.text or "").strip()
        if not raw.lstrip("-").isdigit():
            PENDING_OWNER_ACTIONS[message.from_user.id] = "channel_gifts_view"
            await message.answer(
                "Kanal ID noto'g'ri. Masalan: <code>-1001234567890</code>",
                reply_markup=owner_wait_keyboard(),
            )
            return True
        ok, text = await engine.channel_gift_balance_text(int(raw), auto_create=True)
        await message.answer(
            text,
            reply_markup=owner_channel_gifts_keyboard() if ok else owner_wait_keyboard(),
        )
        if not ok:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "channel_gifts_view"
        return True

    if action == "channel_gifts_grant":
        parts = (message.text or "").split()
        if len(parts) != 3 or not all(part.lstrip("-").isdigit() for part in parts):
            PENDING_OWNER_ACTIONS[message.from_user.id] = "channel_gifts_grant"
            await message.answer(
                "Format noto'g'ri.\n"
                "<code>kanal_id dollar diamond</code>\n"
                "Masalan: <code>-1001234567890 0 500</code>",
                reply_markup=owner_wait_keyboard(),
            )
            return True
        channel_id = int(parts[0])
        dollar = int(parts[1])
        diamonds = int(parts[2])
        ok, text = await engine.grant_channel_balance(
            channel_id,
            dollar=dollar,
            diamonds=diamonds,
            channel_title="",
        )
        await message.answer(
            text,
            reply_markup=owner_channel_gifts_keyboard() if ok else owner_wait_keyboard(),
        )
        if not ok:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "channel_gifts_grant"
        return True

    if action == "channel_gifts_start":
        raw = (message.text or "").strip()
        if not raw.lstrip("-").isdigit():
            PENDING_OWNER_ACTIONS[message.from_user.id] = "channel_gifts_start"
            await message.answer(
                "Kanal ID noto'g'ri. Masalan: <code>-1001234567890</code>",
                reply_markup=owner_wait_keyboard(),
            )
            return True
        channel_id = int(raw)
        if channel_id >= 0:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "channel_gifts_start"
            await message.answer(
                "Kanal ID manfiy bo'lishi kerak. Masalan: <code>-1001234567890</code>",
                reply_markup=owner_wait_keyboard(),
            )
            return True
        if not await engine.bot_is_admin(message.bot, channel_id):
            PENDING_OWNER_ACTIONS[message.from_user.id] = "channel_gifts_start"
            await message.answer(
                "Bot bu kanalda admin emas yoki kanal topilmadi.",
                reply_markup=owner_wait_keyboard(),
            )
            return True
        PENDING_CHANNEL_GIFTS[message.from_user.id] = {"channel_id": channel_id}
        await message.answer(
            "🎛 <b>Tarqatish turini tanlang</b>\n\n"
            "🎁 Tez tarqatish - har bir bosgan user 1 💎 oladi, olmos tugaguncha davom etadi.\n"
            "🎲 Ro'yxatdan o'tish - userlar qatnashadi, admin yakunlaganda bitta g'olib barcha olmosni oladi.",
            reply_markup=owner_channel_gift_mode_keyboard(),
        )
        return True

    if action == "channel_gifts_amount":
        data = PENDING_CHANNEL_GIFTS.get(message.from_user.id)
        raw_amount = (message.text or "").strip()
        if not data or "channel_id" not in data or "mode" not in data:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "channel_gifts_start"
            await message.answer(
                "Ma'lumotlar topilmadi. Qaytadan kanal ID yuboring.",
                reply_markup=owner_wait_keyboard(),
            )
            return True
        if not raw_amount.isdigit():
            PENDING_OWNER_ACTIONS[message.from_user.id] = "channel_gifts_amount"
            await message.answer("Miqdor faqat son bo'lishi kerak. Masalan: <code>100</code>", reply_markup=owner_wait_keyboard())
            return True
        channel_id = int(data["channel_id"])
        mode = str(data["mode"])
        amount = int(raw_amount)
        await message.answer("⏳ Kanalga almaz tarqatish posti yuborilmoqda...")
        try:
            ok, text = await engine.start_channel_diamond_distribution(
                message.bot,
                channel_id=channel_id,
                mode=mode,
                amount=amount,
            )
        except Exception as exc:
            ok = False
            text = f"Xatolik yuz berdi: {escape(str(exc))}"
        await message.answer(
            text,
            reply_markup=owner_channel_gifts_keyboard() if ok else owner_wait_keyboard(),
        )
        if ok:
            PENDING_CHANNEL_GIFTS.pop(message.from_user.id, None)
        else:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "channel_gifts_amount"
        return True

    if action == "premium_timer":
        ok, text = await engine.set_premium_reset_interval_minutes(message.text or "")
        if not ok:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "premium_timer"
            await message.answer(text, reply_markup=owner_wait_keyboard())
            return True
        groups = await engine.premium_groups(include_inactive=True)
        await message.answer(
            f"{text}\n\n{await engine.owner_premium_groups_manage_text()}",
            reply_markup=owner_premium_groups_keyboard(groups),
        )
        return True

    if action == "premium_block_user":
        ok, text = await engine.block_premium_user(message.text or "", blocked_by=message.from_user.id)
        groups = await engine.premium_groups(include_inactive=True) if ok else []
        await message.answer(text, reply_markup=owner_premium_groups_keyboard(groups) if ok else owner_wait_keyboard())
        if not ok:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "premium_block_user"
        return True

    if action == "premium_unblock_user":
        ok, text = await engine.unblock_premium_user(message.text or "")
        groups = await engine.premium_groups(include_inactive=True) if ok else []
        await message.answer(text, reply_markup=owner_premium_groups_keyboard(groups) if ok else owner_wait_keyboard())
        if not ok:
            PENDING_OWNER_ACTIONS[message.from_user.id] = "premium_unblock_user"
        return True

    if action in {"broadcast_users", "broadcast_groups"}:
        target = "users" if action == "broadcast_users" else "groups"
        label = "userlarga" if target == "users" else "guruhlarga"
        await message.answer(f"📣 Xabar {label} tarqatilmoqda...")
        sent, failed = await engine.broadcast_message(
            bot=message.bot,
            target=target,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        await message.answer(f"✅ Yuborildi: {sent}\n⚠️ Xato: {failed}", reply_markup=owner_panel_keyboard())
        return True

    if action == "grant":
        parts = (message.text or "").split()
        if len(parts) != 3 or not all(part.lstrip("-").isdigit() for part in parts):
            PENDING_OWNER_ACTIONS[message.from_user.id] = "grant"
            await message.answer(
                "Format noto'g'ri. Shunday yuboring:\n"
                "<code>telegram_id dollar diamond</code>",
                reply_markup=owner_wait_keyboard(),
            )
            return True
        ok, text = await engine.grant_balance(int(parts[0]), int(parts[1]), int(parts[2]))
        await message.answer(text, reply_markup=owner_panel_keyboard())
        return True

    return False


@router.message()
async def enforce_active_game_chat(message: Message, engine: GameEngine, settings: Settings) -> None:
    if message.chat.type == "private":
        if await _handle_pending_owner_message(message, engine, settings):
            return
        if message.from_user and message.text and not message.text.startswith("/"):
            handled = await engine.handle_pending_last_words(
                bot=message.bot,
                telegram_id=message.from_user.id,
                words=message.text,
            )
            if handled:
                await message.answer("So'nggi xabaringiz guruhga yuborildi.")
                return
            if await engine.can_send_private_team_message(message.from_user.id):
                ok, text = await engine.send_team_message_to_group(
                    bot=message.bot,
                    telegram_id=message.from_user.id,
                    message_text=message.text,
                )
                if ok:
                    await message.answer("✅ " + text)
                elif "aktiv o'yinda" not in text:
                    await message.answer(text)
        return
    if message.from_user is None or message.from_user.is_bot:
        return
    if message.text and message.text.startswith("/"):
        return
    if message.text and message.text.lstrip().startswith("!"):
        return

    should_delete = await engine.should_delete_message_for_non_player(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
    )
    if not should_delete:
        return

    try:
        await message.delete()
    except TelegramBadRequest:
        return
