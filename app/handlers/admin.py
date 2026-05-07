from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.game_engine import GameEngine
from app.keyboards import owner_panel_keyboard, owner_premium_groups_keyboard, owner_wait_keyboard

router = Router()
PENDING_OWNER_ACTIONS: dict[int, str] = {}
PENDING_PREMIUM_GROUPS: dict[int, dict[str, str | int]] = {}


def _is_owner(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


def _owner_panel_text(stats: str) -> str:
    return (
        "🛡 <b>Owner admin panel</b>\n\n"
        f"{stats}\n\n"
        "Barcha admin amallar tugmalar orqali ishlaydi. "
        "Premium guruh qo'shish, reklama va kredit amallari panel ichidan boshqariladi."
    )


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
    if callback.message:
        await callback.message.edit_text(_owner_panel_text(stats), reply_markup=owner_panel_keyboard())
    await callback.answer()


@router.callback_query(F.data == "owner:stats")
async def owner_stats_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS.pop(callback.from_user.id, None)
    if callback.message:
        await callback.message.edit_text(await engine.owner_stats(), reply_markup=owner_panel_keyboard())
    await callback.answer()


@router.callback_query(F.data == "owner:premium_groups")
async def owner_premium_groups_callback(callback: CallbackQuery, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_text(
            "🎲 <b>Premium guruhlar boshqaruvi</b>\n\n"
            "Bu yerda userlar olmos orqali qo'shiladigan premium guruhlarni ulaysiz.",
            reply_markup=owner_premium_groups_keyboard(),
        )
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
    if callback.message:
        await callback.message.edit_text(
            await engine.premium_groups_text(include_inactive=True),
            reply_markup=owner_premium_groups_keyboard(),
        )
    await callback.answer()


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
            "🎲 Premium guruhlar - nom, link va olmos narxi bilan premium guruh ulaydi.\n"
            "📣 Userlarga reklama - keyingi oddiy xabarni userlarga tarqatadi.\n"
            "🏘 Guruhlarga reklama - keyingi oddiy xabarni guruhlarga tarqatadi.\n"
            "🎁 Kredit berish - user balansiga dollar/olmos qo'shadi.\n\n"
            "Slash broadcast buyruqlari ishlatilmaydi, hammasi tugma orqali.",
            reply_markup=owner_panel_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "owner:cancel")
async def owner_cancel_callback(callback: CallbackQuery, engine: GameEngine, settings: Settings) -> None:
    if callback.from_user is None or not _is_owner(callback.from_user.id, settings):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    PENDING_OWNER_ACTIONS.pop(callback.from_user.id, None)
    PENDING_PREMIUM_GROUPS.pop(callback.from_user.id, None)
    if callback.message:
        await callback.message.edit_text(_owner_panel_text(await engine.owner_stats()), reply_markup=owner_panel_keyboard())
    await callback.answer("Bekor qilindi.")


async def _handle_pending_owner_message(message: Message, engine: GameEngine, settings: Settings) -> bool:
    if message.from_user is None or not _is_owner(message.from_user.id, settings):
        return False
    action = PENDING_OWNER_ACTIONS.pop(message.from_user.id, None)
    if action is None:
        return False

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
            f"💎 Narx: <b>{group.diamond_price}</b>",
            reply_markup=owner_premium_groups_keyboard(),
        )
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
    if message.from_user is None or message.from_user.is_bot:
        return
    if message.text and message.text.startswith("/"):
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
