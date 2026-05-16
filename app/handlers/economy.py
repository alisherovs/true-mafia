from __future__ import annotations

import json
import asyncio
import random
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, PreCheckoutQuery, LabeledPrice
from aiogram.utils.formatting import CustomEmoji, Text, TextLink
from sqlalchemy import select

from app.config import Settings
from app.database import SessionLocal
from app.game_engine import GameEngine
from app.keyboards import (
    diamond_shop_keyboard,
    disable_role_shop_keyboard,
    dollar_exchange_keyboard,
    gift_confirm_keyboard,
    gift_shop_keyboard,
    premium_confirm_keyboard,
    premium_shop_keyboard,
    role_shop_keyboard,
    shop_keyboard,
    vip_keyboard,
)
from app.models import DiamondGiveaway, DiamondTransaction, DollarTransaction, User
from app.texts import t

router = Router()
DIAMOND_EMOJI_ID = "5427168083074628963"
DOLLAR_EMOJI_ID = "5409048419211682843"
LARGE_TRANSFER_THRESHOLD = 5000


def _user_link(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(name)}</a>'


def _user_text_link(user_id: int, name: str) -> TextLink:
    return TextLink(name or str(user_id), url=f"tg://user?id={user_id}")


def _diamond_transfer_kwargs(
    sender_id: int,
    sender_name: str,
    target_id: int,
    target_name: str,
    amount: int,
    note: str,
) -> dict:
    parts = [
        _user_text_link(sender_id, sender_name),
        " ➔ ",
        _user_text_link(target_id, target_name),
        ": ",
        CustomEmoji("💎", custom_emoji_id=DIAMOND_EMOJI_ID),
        f" {amount} olmos",
    ]
    if note and note != "-":
        parts.append(f"\nIzoh: {note}")
    return Text(*parts).as_kwargs()


def _record_diamond_transaction(
    session,
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
            note=note or None,
            counterparty_telegram_id=counterparty.telegram_id if counterparty else None,
            counterparty_name=(counterparty.display_name or "User")[:255] if counterparty else None,
            chat_id=chat_id,
        )
    )


def _large_transfer_notice(
    *,
    currency_icon: str,
    amount: int,
    sender_name: str,
    sender_id: int,
    target_name: str,
    target_id: int,
    chat_title: str,
    chat_id: int,
) -> str:
    return (
        f"{currency_icon} {amount} o'tkazma aniqlandi.\n"
        f"    💸 O'tkazuvchi: {escape(sender_name or str(sender_id))} {sender_id}\n"
        f"    🎯 Qabul qiluvchi: {escape(target_name or str(target_id))} {target_id}\n"
        f"    🏠 Guruh: {escape(chat_title or 'Private')} ({chat_id})"
    )


def _giveaway_keyboard(giveaway_id: int, active: bool = True) -> Optional[InlineKeyboardMarkup]:
    if not active:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Qatnashish", callback_data=f"giveaway:join:{giveaway_id}")],
            [InlineKeyboardButton(text="✅ Yakunlash", callback_data=f"giveaway:finish:{giveaway_id}")],
        ]
    )


def _giveaway_participants(raw: str) -> list[dict[str, object]]:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    participants: list[dict[str, object]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        user_id = item.get("id")
        name = str(item.get("name") or user_id or "User")
        if isinstance(user_id, int):
            participants.append({"id": user_id, "name": name[:255]})
    return participants


def _giveaway_text(creator: User, giveaway: DiamondGiveaway) -> str:
    participants = _giveaway_participants(giveaway.participants_json)
    if participants:
        lines = [
            f"{idx}) {_user_link(int(item['id']), str(item['name']))}"
            for idx, item in enumerate(participants, 1)
        ]
        participants_text = "\n".join(lines)
    else:
        participants_text = "-"
    creator_name = _user_link(creator.telegram_id, creator.display_name or str(creator.telegram_id))
    return (
        f"{creator_name} kimgadir {giveaway.amount} ta <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> sovg'a qilmoqchi!\n\n"
        f"Ishtirokchilar:\n{participants_text}\n\n"
        f"Ishtirokchilar soni: {len(participants)}/50"
    )


async def _finish_giveaway(
    session,
    giveaway: DiamondGiveaway,
    creator: User,
    participants: list[dict[str, object]],
) -> tuple[int, str, str]:
    winner = random.choice(participants)
    winner_id = int(winner["id"])
    winner_name = str(winner["name"])
    winner_user = (
        await session.execute(select(User).where(User.telegram_id == winner_id))
    ).scalar_one_or_none()
    if winner_user is None:
        winner_user = User(
            telegram_id=winner_id,
            display_name=winner_name,
            language="uz",
            language_selected=False,
        )
        session.add(winner_user)
    winner_user.diamonds += giveaway.amount
    _record_diamond_transaction(
        session,
        winner_user,
        giveaway.amount,
        "giveaway_win",
        note=f"Sovg'a #{giveaway.id} yutildi",
        counterparty=creator,
        chat_id=giveaway.chat_id,
    )
    giveaway.status = "finished"
    giveaway.winner_telegram_id = winner_id
    giveaway.ended_at = datetime.now(timezone.utc)
    await session.commit()
    final_text = (
        f"{_giveaway_text(creator, giveaway)}\n\n"
        f"🎉 G'olib: {_user_link(winner_id, winner_name)}\n"
        f"<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {giveaway.amount} olmos hisobiga qo'shildi."
    )
    private_text = f"🎉 Siz sovg'ada yutdingiz!\n<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {giveaway.amount} olmos hisobingizga qo'shildi."
    return winner_id, final_text, private_text


@router.message(Command("shop"))
async def cmd_shop(message: Message, engine: GameEngine) -> None:
    await engine.ensure_user(message.from_user)
    has_hero = await engine.user_has_hero(message.from_user.id) if message.from_user else False
    await message.answer(
        "🛒 <b>Do'kon</b>\n\n"
        "Himoya va maxsus imkoniyatlarni <tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji> dollar yoki <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> almaz orqali sotib olishingiz mumkin.",
        reply_markup=shop_keyboard(has_hero),
    )


@router.callback_query(F.data == "shop:open")
async def shop_open_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    await engine.ensure_user(callback.from_user)
    has_hero = await engine.user_has_hero(callback.from_user.id)
    await callback.message.edit_text(
        "🛒 <b>Do'kon</b>\n\n"
        "Kerakli itemni tanlang. Xarid summasi profilingizdagi <tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji> dollar yoki <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> almazdan yechiladi.",
        reply_markup=shop_keyboard(has_hero),
    )
    await callback.answer()


@router.callback_query(F.data == "shop:roles")
async def shop_roles_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    await callback.message.edit_text(
        "🃏 <b>Keyingi o'yindagi rol</b>\n\n"
        "Tanlangan rol keyingi o'yinda sizga beriladi. Agar balans yetarli bo'lsa, xarid faqat bir marta saqlanadi.",
        reply_markup=role_shop_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "shop:disable_roles")
async def shop_disable_roles_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    await callback.message.edit_text(
        "🚫 <b>Faol rolni o'chirish</b>\n\n"
        "Tanlangan faol rol keyingi o'yin role pool'idan olib tashlanadi.\n"
        "Narx: <b><tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji> 100</b>",
        reply_markup=disable_role_shop_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shop:buy:"))
async def shop_buy_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    item_key = callback.data.split(":", maxsplit=2)[2]
    ok, text = await engine.buy_shop_item(callback.from_user.id, item_key)
    await callback.answer(text, show_alert=True)


@router.callback_query(F.data.startswith("shop:disable_role:"))
async def shop_disable_role_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    role_key = callback.data.split(":", maxsplit=2)[2]
    ok, text = await engine.buy_shop_item(callback.from_user.id, f"disable_role:{role_key}")
    await callback.answer(text, show_alert=True)


@router.callback_query(F.data.startswith("shop:role:"))
async def shop_role_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    role_key = callback.data.split(":", maxsplit=2)[2]
    ok, text = await engine.buy_shop_item(callback.from_user.id, f"role:{role_key}")
    await callback.answer(text, show_alert=True)


@router.callback_query(F.data == "dollar:shop")
async def dollar_shop_open(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    await engine.ensure_user(callback.from_user)
    await callback.message.edit_text(
        "<tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji> <b>Dollar olish</b>\n\n"
        "Bu bo'limda faqat almazni dollarga almashtirasiz.\n"
        "Kurs: <b><tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> 1 almaz = <tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji> 500 dollar</b>",
        reply_markup=dollar_exchange_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dollar:exchange:"))
async def dollar_exchange_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    raw_amount = callback.data.split(":", maxsplit=2)[2]
    amount: int | str = "all" if raw_amount == "all" else int(raw_amount)
    ok, text = await engine.exchange_diamonds_to_dollars(callback.from_user.id, amount)
    await callback.answer(text, show_alert=True)


@router.message(Command("gsend"))
async def cmd_gsend(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type == "private":
        await message.answer("Bu buyruq guruhda ishlaydi: /gsend 10")
        return

    raw_amount = (command.args or "").strip()
    if not raw_amount.isdigit():
        await message.reply("Foydalanish: <code>/gsend 10</code>\nKurs: yuborilgan <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> premium guruh reytingiga qo'shiladi.")
        return

    ok, text = await engine.contribute_premium_group(
        bot=message.bot,
        chat_id=message.chat.id,
        chat_title=message.chat.title or "Group",
        tg_user=message.from_user,
        diamonds=int(raw_amount),
    )
    await message.reply(text)


@router.message(Command("gbust"))
async def cmd_gbust(message: Message, engine: GameEngine) -> None:
    if message.chat.type == "private":
        return
    if not await engine.bot_is_admin(message.bot, message.chat.id):
        return

    ok, text = await engine.bankrupt_premium_group_by_chat(message.chat.id)
    await message.reply(text)


async def _burn_user_balance(message: Message, settings: Settings, field: str, label: str) -> None:
    if message.from_user is None or message.from_user.id not in settings.admin_ids:
        return
    if message.reply_to_message is None or message.reply_to_message.from_user is None:
        await message.reply("Bu buyruqni player xabariga reply qilib ishlating.")
        return

    target_tg = message.reply_to_message.from_user
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == target_tg.id))
        ).scalar_one_or_none()
        if user is None:
            await message.reply("Foydalanuvchi topilmadi. U avval /start bosgan bo'lishi kerak.")
            return

        burned = int(getattr(user, field, 0) or 0)
        setattr(user, field, 0)
        if field == "diamonds":
            _record_diamond_transaction(
                session,
                user,
                -burned,
                "admin_bust",
                note=f"Admin tomonidan olmoslar 0 qilindi: admin={message.from_user.id}",
                chat_id=message.chat.id,
            )
        await session.commit()

    target_name = _user_link(target_tg.id, user.display_name or target_tg.full_name or str(target_tg.id))
    await message.reply(f"🔥 {target_name} balansidagi {label} kuyib ketdi.\nMiqdor: <b>{burned}</b>")


@router.message(Command("bust1"))
async def cmd_bust_diamonds(message: Message, settings: Settings) -> None:
    await _burn_user_balance(message, settings, "diamonds", "<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> olmoslar")


@router.message(Command("bust2"))
async def cmd_bust_dollars(message: Message, settings: Settings) -> None:
    await _burn_user_balance(message, settings, "dollar", "<tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji> dollarlar")


@router.message(Command("give", "giveto"))
async def cmd_give(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    sender = await engine.ensure_user(message.from_user)
    lang = sender.language

    target_id: Optional[int] = None
    amount: Optional[int] = None
    note = ""
    raw_args = (command.args or "").strip()

    if message.reply_to_message and message.reply_to_message.from_user and command.args:
        parts = command.args.split(maxsplit=1)
        if parts and parts[0].isdigit():
            target_id = message.reply_to_message.from_user.id
            amount = int(parts[0])
            note = parts[1].strip() if len(parts) > 1 else ""
    elif command.args:
        parts = command.args.split()
        if len(parts) >= 2 and parts[1].isdigit():
            amount = int(parts[1])
            raw_target = parts[0].strip()
            note = " ".join(parts[2:]).strip()
            if raw_target.startswith("@"):
                username = raw_target[1:]
                async with SessionLocal() as session:
                    target = (await session.execute(select(User).where(User.username == username))).scalar_one_or_none()
                    target_id = target.telegram_id if target else None
            elif raw_target.isdigit():
                target_id = int(raw_target)

    if target_id is None or amount is None:
        return

    if target_id == sender.telegram_id:
        await message.reply("O'zingizga yubora olmaysiz.")
        return

    async with SessionLocal() as session:
        target = (await session.execute(select(User).where(User.telegram_id == target_id))).scalar_one_or_none()
        if target is None:
            await message.reply("Target foydalanuvchi topilmadi. U /start qilishi kerak.")
            return

    ok, status = await engine.transfer_diamonds(sender.telegram_id, target_id, amount)
    if not ok:
        if "Balans" in status:
            await message.reply(t(lang, "give_not_enough"))
        else:
            await message.reply(status)
        return

    sender_display = sender.display_name or message.from_user.full_name or str(sender.telegram_id)
    target_display = target.display_name or str(target.telegram_id)
    note_text = note if note else "-"
    transfer_kwargs = _diamond_transfer_kwargs(
        sender.telegram_id,
        sender_display,
        target.telegram_id,
        target_display,
        amount,
        note_text,
    )
    await message.reply(**transfer_kwargs)
    try:
        await message.bot.send_message(target_id, **transfer_kwargs)
    except Exception:
        pass


def _dollar_transfer_kwargs(
    sender_id: int,
    sender_name: str,
    target_id: int,
    target_name: str,
    amount: int,
    note: str,
) -> dict:
    parts = [
        _user_text_link(sender_id, sender_name),
        " ➔ ",
        _user_text_link(target_id, target_name),
        ": ",
        CustomEmoji("💵", custom_emoji_id=DOLLAR_EMOJI_ID),
        f" {amount} dollar",
    ]
    if note and note != "-":
        parts.append(f"\nIzoh: {note}")
    return Text(*parts).as_kwargs()


@router.message(Command("money"))
async def cmd_money(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    sender = await engine.ensure_user(message.from_user)
    lang = sender.language

    target_id: Optional[int] = None
    amount: Optional[int] = None
    note = ""
    raw_args = (command.args or "").strip()

    if message.reply_to_message and message.reply_to_message.from_user and command.args:
        parts = command.args.split(maxsplit=1)
        if parts and parts[0].isdigit():
            target_id = message.reply_to_message.from_user.id
            amount = int(parts[0])
            note = parts[1].strip() if len(parts) > 1 else ""
    elif command.args:
        parts = command.args.split()
        if len(parts) >= 2 and parts[1].isdigit():
            amount = int(parts[1])
            raw_target = parts[0].strip()
            note = " ".join(parts[2:]).strip()
            if raw_target.startswith("@"):
                username = raw_target[1:]
                async with SessionLocal() as session:
                    target = (await session.execute(select(User).where(User.username == username))).scalar_one_or_none()
                    target_id = target.telegram_id if target else None
            elif raw_target.isdigit():
                target_id = int(raw_target)

    if target_id is None or amount is None:
        return

    if target_id == sender.telegram_id:
        await message.reply("O'zingizga yubora olmaysiz.")
        return

    async with SessionLocal() as session:
        target = (await session.execute(select(User).where(User.telegram_id == target_id))).scalar_one_or_none()
        if target is None:
            await message.reply("Target foydalanuvchi topilmadi. U /start qilishi kerak.")
            return

    ok, status = await engine.transfer_dollars(sender.telegram_id, target_id, amount)
    if not ok:
        if "Balans" in status:
            await message.reply(t(lang, "give_not_enough"))
        else:
            await message.reply(status)
        return

    sender_display = sender.display_name or message.from_user.full_name or str(sender.telegram_id)
    target_display = target.display_name or str(target.telegram_id)
    note_text = note if note else "-"
    transfer_kwargs = _dollar_transfer_kwargs(
        sender.telegram_id,
        sender_display,
        target.telegram_id,
        target_display,
        amount,
        note_text,
    )
    await message.reply(**transfer_kwargs)
    try:
        await message.bot.send_message(target_id, **transfer_kwargs)
    except Exception:
        pass


@router.message(Command("change"))
async def cmd_change(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type == "private":
        return
    sender = await engine.ensure_user(message.from_user)
    lang = sender.language
    raw_args = (command.args or "").strip()
    if not raw_args.isdigit():
        return
    amount = int(raw_args)
    if amount < 2:
        await message.reply("Minimal miqdor 2 olmos.")
        return
    if sender.diamonds < amount:
        await message.reply(t(lang, "give_not_enough"))
        return
    async with SessionLocal() as session:
        fresh_sender = (
            await session.execute(select(User).where(User.telegram_id == sender.telegram_id))
        ).scalar_one_or_none()
        if fresh_sender is None:
            await message.reply("Avval /start bosing.")
            return
        if fresh_sender.diamonds < amount:
            await message.reply(t(lang, "give_not_enough"))
            return
        fresh_sender.diamonds -= amount
        _record_diamond_transaction(
            session,
            fresh_sender,
            -amount,
            "giveaway_create",
            note="Guruhda almaz sovg'asi ochildi",
            chat_id=message.chat.id,
        )
        giveaway = DiamondGiveaway(
            chat_id=message.chat.id,
            creator_telegram_id=sender.telegram_id,
            amount=amount,
            participants_json="[]",
        )
        session.add(giveaway)
        await session.commit()
        text = _giveaway_text(fresh_sender, giveaway)

    sent = await message.answer(text, reply_markup=_giveaway_keyboard(giveaway.id))
    async with SessionLocal() as session:
        row = (await session.execute(select(DiamondGiveaway).where(DiamondGiveaway.id == giveaway.id))).scalar_one_or_none()
        if row:
            row.message_id = sent.message_id
            await session.commit()


@router.message(Command("send"))
async def cmd_send(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type == "private":
        return
    sender = await engine.ensure_user(message.from_user)
    lang = sender.language
    raw_args = (command.args or "").strip()
    if not raw_args.isdigit():
        return
    amount = int(raw_args)
    if amount < 1:
        await message.reply("Miqdor musbat bo'lishi kerak.")
        return
    if sender.diamonds < amount:
        await message.reply(t(lang, "give_not_enough"))
        return
    async with SessionLocal() as session:
        fresh_sender = (
            await session.execute(select(User).where(User.telegram_id == sender.telegram_id))
        ).scalar_one_or_none()
        if fresh_sender is None:
            await message.reply("Avval /start bosing.")
            return
        if fresh_sender.diamonds < amount:
            await message.reply(t(lang, "give_not_enough"))
            return
        fresh_sender.diamonds -= amount
        _record_diamond_transaction(
            session,
            fresh_sender,
            -amount,
            "send_gift_create",
            note=f"Guruhda sovg'a ochildi: {amount} olmos",
            chat_id=message.chat.id,
        )
        giveaway = DiamondGiveaway(
            chat_id=message.chat.id,
            creator_telegram_id=sender.telegram_id,
            amount=amount,
            participants_json="[]",
            status="send_active",
        )
        session.add(giveaway)
        await session.commit()
        giveaway_id = giveaway.id
        sender_name = fresh_sender.display_name or message.from_user.full_name or str(sender.telegram_id)

    remaining = amount
    text = (
        f'{_user_link(sender.telegram_id, sender_name)} guruhga {amount} ta 💎 sovg\'a qildi!\n\n'
        f'💎 Qoldi: {remaining}/{amount} — 1 ta olish uchun bosing.'
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🎁 1 💎 olish", callback_data=f"sendgift:claim:{giveaway_id}")]
        ]
    )
    sent = await message.answer(text, reply_markup=kb)
    async with SessionLocal() as session:
        row = (await session.execute(select(DiamondGiveaway).where(DiamondGiveaway.id == giveaway_id))).scalar_one_or_none()
        if row:
            row.message_id = sent.message_id
            await session.commit()


@router.callback_query(F.data.startswith("sendgift:claim:"))
async def sendgift_claim_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer("Bad callback", show_alert=True)
        return
    giveaway_id = int(parts[2])

    async with SessionLocal() as session:
        giveaway = (
            await session.execute(select(DiamondGiveaway).where(DiamondGiveaway.id == giveaway_id))
        ).scalar_one_or_none()
        if giveaway is None or giveaway.status != "send_active":
            await callback.answer("Bu sovg'a allaqachon tugagan yoki mavjud emas.", show_alert=True)
            return
        if callback.from_user.id == giveaway.creator_telegram_id:
            await callback.answer("O'zingiz yuborgan sovg'ani o'zingiz ololmaysiz.", show_alert=True)
            return

        participants = _giveaway_participants(giveaway.participants_json)
        if any(int(item["id"]) == callback.from_user.id for item in participants):
            await callback.answer("Siz allaqachon 1 💎 oldingiz.", show_alert=True)
            return

        claimer = await engine.ensure_user(callback.from_user)
        claimer_user = (
            await session.execute(select(User).where(User.telegram_id == claimer.telegram_id))
        ).scalar_one_or_none()
        creator = (
            await session.execute(select(User).where(User.telegram_id == giveaway.creator_telegram_id))
        ).scalar_one_or_none()
        if claimer_user is None or creator is None:
            await callback.answer("Foydalanuvchi topilmadi.", show_alert=True)
            return

        claimer_user.diamonds = (claimer_user.diamonds or 0) + 1
        _record_diamond_transaction(
            session,
            claimer_user,
            1,
            "send_gift_claim",
            note=f"Sovg'a #{giveaway.id} dan 1 olmos olindi",
            counterparty=creator,
            chat_id=giveaway.chat_id,
        )
        participants.append({"id": callback.from_user.id, "name": claimer.display_name or callback.from_user.full_name})
        giveaway.participants_json = json.dumps(participants, ensure_ascii=False)

        claimed_count = len(participants)
        total = giveaway.amount
        remaining = total - claimed_count
        creator_name = creator.display_name or str(creator.telegram_id)

        if remaining <= 0:
            giveaway.status = "finished"
            giveaway.ended_at = datetime.now(timezone.utc)
            giveaway.winner_telegram_id = callback.from_user.id
            await session.commit()

            lines = [f"{i+1}) {_user_link(int(p['id']), p['name'])} 1💎" for i, p in enumerate(participants)]
            final_text = (
                f'{_user_link(creator.telegram_id, creator_name)} ajratgan sovg\'alar tugadi!\n\n'
                f'Olganlar:\n' + "\n".join(lines)
            )
            try:
                await callback.message.edit_text(final_text, reply_markup=None)
            except TelegramBadRequest:
                await callback.message.answer(final_text)
            await callback.answer("Tabriklaymiz! 1 💎 olmos oldingiz!", show_alert=True)
        else:
            await session.commit()

            lines = [f"{i+1}) {_user_link(int(p['id']), p['name'])} 1💎" for i, p in enumerate(participants)]
            progress_text = (
                f'{_user_link(creator.telegram_id, creator_name)} guruhga {total} ta 💎 sovg\'a qildi!\n\n'
                f'💎 Qoldi: {remaining}/{total} — 1 ta olish uchun bosing.\n\n'
                f'Olganlar:\n' + "\n".join(lines)
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🎁 1 💎 olish", callback_data=f"sendgift:claim:{giveaway_id}")]
                ]
            )
            try:
                await callback.message.edit_text(progress_text, reply_markup=kb)
            except TelegramBadRequest:
                pass
            await callback.answer("Tabriklaymiz! 1 💎 olmos oldingiz!", show_alert=True)


@router.callback_query(F.data.startswith("giveaway:"))
async def giveaway_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer("Bad callback", show_alert=True)
        return
    action = parts[1]
    giveaway_id = int(parts[2])

    async with SessionLocal() as session:
        giveaway = (
            await session.execute(select(DiamondGiveaway).where(DiamondGiveaway.id == giveaway_id))
        ).scalar_one_or_none()
        if giveaway is None or giveaway.status != "active":
            await callback.answer("Sovg'a yakunlangan yoki topilmadi.", show_alert=True)
            return
        creator = (
            await session.execute(select(User).where(User.telegram_id == giveaway.creator_telegram_id))
        ).scalar_one_or_none()
        if creator is None:
            await callback.answer("Sovg'a egasi topilmadi.", show_alert=True)
            return

        if action == "join":
            if callback.from_user.id == giveaway.creator_telegram_id:
                await callback.answer("O'zingiz ochgan sovg'ada qatnasha olmaysiz.", show_alert=True)
                return
            user = await engine.ensure_user(callback.from_user)
            participants = _giveaway_participants(giveaway.participants_json)
            if any(int(item["id"]) == callback.from_user.id for item in participants):
                await callback.answer("Siz allaqachon qatnashyapsiz.", show_alert=True)
                return
            if len(participants) >= 50:
                await callback.answer("Ishtirokchilar soni 50 taga yetgan.", show_alert=True)
                return
            participants.append({"id": callback.from_user.id, "name": user.display_name or callback.from_user.full_name})
            giveaway.participants_json = json.dumps(participants, ensure_ascii=False)
            if len(participants) >= 50:
                winner_id, final_text, private_text = await _finish_giveaway(session, giveaway, creator, participants)
                try:
                    await callback.message.edit_text(final_text, reply_markup=None)
                except TelegramBadRequest:
                    await callback.message.answer(final_text)
                try:
                    await callback.bot.send_message(winner_id, private_text)
                except Exception:
                    pass
                await callback.answer("Qatnashdingiz. 50 ta ishtirokchi to'ldi va sovg'a yakunlandi.")
            else:
                text = _giveaway_text(creator, giveaway)
                await session.commit()
                try:
                    await callback.message.edit_text(text, reply_markup=_giveaway_keyboard(giveaway.id))
                except TelegramBadRequest:
                    pass
                await callback.answer("Qatnashdingiz.")
            return

        if action == "finish":
            if callback.from_user.id != giveaway.creator_telegram_id:
                await callback.answer("Sovg'ani faqat uni uyushtirgan odam yakunlay oladi.", show_alert=True)
                return
            participants = _giveaway_participants(giveaway.participants_json)
            if not participants:
                creator.diamonds += giveaway.amount
                _record_diamond_transaction(
                    session,
                    creator,
                    giveaway.amount,
                    "giveaway_refund",
                    note=f"Sovg'a #{giveaway.id}: ishtirokchi yo'q, qaytarildi",
                    chat_id=giveaway.chat_id,
                )
                giveaway.status = "cancelled"
                giveaway.ended_at = datetime.now(timezone.utc)
                await session.commit()
                await callback.message.edit_text(
                    f"{_user_link(creator.telegram_id, creator.display_name)} sovg'asi bekor qilindi.\n"
                    "Ishtirokchi yo'q edi, olmos egasiga qaytarildi.",
                    reply_markup=None,
                )
                await callback.answer("Bekor qilindi.")
                return

            winner_id, final_text, private_text = await _finish_giveaway(session, giveaway, creator, participants)
            try:
                await callback.message.edit_text(final_text, reply_markup=None)
            except TelegramBadRequest:
                await callback.message.answer(final_text)
            try:
                await callback.bot.send_message(winner_id, private_text)
            except Exception:
                pass
            await callback.answer("Sovg'a yakunlandi.")
            return

    await callback.answer("Noma'lum amal.", show_alert=True)


# Diamond shop handlers
@router.callback_query(F.data == "diamond:shop")
async def diamond_shop_open(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    admin_username = await engine.get_purchase_admin_username()
    await callback.message.edit_text(
        "<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> <b>Almaz xaridi</b>\n\n"
        "Kerakli almaz paketini tanlang. Telegram Stars orqali to'lov qilishingiz mumkin.",
        reply_markup=diamond_shop_keyboard(admin_username),
    )
    await callback.answer()


# Diamond packages: amount -> (diamonds, stars)
DIAMOND_PACKAGES = {
    "1": (1, 7),
    "10": (10, 70),
    "30": (30, 200),
    "70": (70, 450),
    "250": (250, 1300),
    "1000": (1000, 5000),
}


@router.callback_query(F.data.startswith("diamond:buy:"))
async def diamond_buy(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    
    package_key = callback.data.split(":", maxsplit=2)[2]
    if package_key not in DIAMOND_PACKAGES:
        await callback.answer("Noto'g'ri paket.", show_alert=True)
        return
    
    diamonds, stars = DIAMOND_PACKAGES[package_key]
    
    try:
        await engine.ensure_user(callback.from_user)
        await callback.bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"💎 {diamonds} almaz",
            description=f"{diamonds} ta almaz sotib olish",
            payload=f"diamonds:{diamonds}:{callback.from_user.id}",
            currency="XTR",
            prices=[LabeledPrice(label=f"💎 {diamonds} almaz", amount=stars)],
            provider_token="",
        )
        await callback.answer("Invoice jo'natildi!")
    except Exception as e:
        await callback.answer(f"Invoice yaratilmadi: {str(e)}", show_alert=True)


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    """Confirm pre-checkout query for star payments."""
    if pre_checkout_query.invoice_payload.startswith("diamonds:") or pre_checkout_query.invoice_payload.startswith("vip:"):
        await pre_checkout_query.answer(ok=True)
    else:
        await pre_checkout_query.answer(ok=False, error_message="Noto'g'ri to'lov so'rovi")


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, engine: GameEngine) -> None:
    """Process successful star payment."""
    if message.successful_payment is None:
        return
    
    payload = message.successful_payment.invoice_payload
    if payload.startswith("vip:"):
        try:
            _, raw_user_id = payload.split(":", maxsplit=1)
            buyer_id = int(raw_user_id)
        except (IndexError, ValueError):
            await message.answer("❌ To'lov xatosi: Noto'g'ri qiymat")
            return
        if message.from_user is None:
            await message.answer("❌ To'lov xatosi: foydalanuvchi topilmadi.")
            return
        if buyer_id != message.from_user.id:
            await message.answer("❌ To'lov xatosi: foydalanuvchi mos kelmadi.")
            return
        await engine.ensure_user(message.from_user)
        async with SessionLocal() as session:
            user = (await session.execute(
                select(User).where(User.telegram_id == buyer_id)
            )).scalar_one_or_none()
            if user is None:
                await message.answer("❌ Foydalanuvchi topilmadi.")
                return
            now = _utc_now()
            current_vip_until = _as_aware_utc(user.vip_until)
            if current_vip_until and current_vip_until > now:
                user.vip_until = current_vip_until + timedelta(days=30)
            else:
                user.vip_until = now + timedelta(days=30)
            await session.commit()
        await message.answer("✅ <b>VIP User faollashtirildi!</b>\n\nMuddat: <b>30 kun</b>")
        return

    if not payload.startswith("diamonds:"):
        return
    
    try:
        _, raw_diamonds, raw_user_id = payload.split(":", maxsplit=2)
        diamonds = int(raw_diamonds)
        buyer_id = int(raw_user_id)
    except (IndexError, ValueError):
        await message.answer("❌ To'lov xatosi: Noto'g'ri qiymat")
        return
    if message.from_user is None:
        await message.answer("❌ To'lov xatosi: foydalanuvchi topilmadi.")
        return
    if buyer_id not in {0, message.from_user.id}:
        await message.answer("❌ To'lov xatosi: foydalanuvchi mos kelmadi.")
        return
    target_user_id = message.from_user.id if buyer_id == 0 else buyer_id
    await engine.ensure_user(message.from_user)
    
    async with SessionLocal() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == target_user_id)
        )).scalar_one_or_none()
        
        if user is None:
            await message.answer("❌ Foydalanuvchi topilmadi. /start qaytadami?")
            return
        
        user.diamonds = (user.diamonds or 0) + diamonds
        _record_diamond_transaction(
            session,
            user,
            diamonds,
            "diamond_payment",
            note=f"Telegram Stars orqali xarid: payload={payload}",
            chat_id=message.chat.id,
        )
        await session.commit()
    
    await message.answer(
        f"✅ <b>To'lov muvaffaqiyatli!</b>\n\n"
        f"<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> {diamonds} almaz sizning profilingizga qo'shildi!"
    )


# ===== Diamond -> Telegram Gift exchange =====
# Exchange rate: how many Telegram Stars 1 diamond redeems for when buying gifts.
STARS_PER_DIAMOND = 5
# Cap how many gifts to display in the menu to keep the keyboard readable.
GIFT_LIST_LIMIT = 30
# Keep callback handlers responsive even when Telegram's gifts endpoint is slow.
GIFT_FETCH_TIMEOUT_SECONDS = 8

# Telegram Premium subscription plans. Star prices are FIXED by Telegram Bot API
# (see giftPremiumSubscription): 3mo=1000, 6mo=1500, 12mo=2500.
# Diamond cost is set manually based on real-world pricing (~1000 som per 💎).
# Note: 1-month Premium cannot be gifted via Bot API.
PREMIUM_PLANS: list[tuple[int, int, int]] = [
    (3, 1000, 175),   # 175,000 som
    (6, 1500, 230),   # 230,000 som
    (12, 2500, 390),  # 390,000 som
]
PREMIUM_BY_MONTHS: dict[int, tuple[int, int]] = {
    months: (stars, diamonds) for months, stars, diamonds in PREMIUM_PLANS
}


GIFT_FIXED_PRICES: dict[int, int] = {
    15: 8,
    25: 11,
    50: 15,
    100: 28,
}


def _diamonds_for_stars(stars: int) -> int:
    fixed = GIFT_FIXED_PRICES.get(stars)
    if fixed is not None:
        return fixed
    import math
    return max(1, math.ceil(int(stars) / STARS_PER_DIAMOND))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _vip_expires_after(value: datetime | None, now: datetime) -> bool:
    expires_at = _as_aware_utc(value)
    return expires_at is not None and expires_at > now


async def _fetch_sorted_gifts(bot) -> list:
    try:
        gifts_obj = await asyncio.wait_for(
            bot.get_available_gifts(),
            timeout=GIFT_FETCH_TIMEOUT_SECONDS,
        )
    except Exception:
        return []
    gifts = list(getattr(gifts_obj, "gifts", []) or [])
    # Filter out limited gifts that are sold out, then sort by star_count asc.
    filtered = []
    for g in gifts:
        remaining = getattr(g, "remaining_count", None)
        if remaining is not None and remaining <= 0:
            continue
        if int(getattr(g, "star_count", 0) or 0) <= 0:
            continue
        filtered.append(g)
    filtered.sort(key=lambda g: int(getattr(g, "star_count", 0) or 0))
    return filtered[:GIFT_LIST_LIMIT]


@router.callback_query(F.data == "shop:gifts")
async def shop_gifts_open(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    async with SessionLocal() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one_or_none()
        if user is None or not _user_is_vip(user):
            await callback.answer("Bu bo'lim faqat VIP User uchun. Do'kondan VIP User faollashtiring.", show_alert=True)
            return
    await callback.answer("Sovg'alar yuklanmoqda...")
    gifts = await _fetch_sorted_gifts(callback.bot)
    if not gifts:
        await callback.message.edit_text(
            "🎁 <b>Telegram sovg'alari</b>\n\n"
            "Hozircha mavjud sovg'alar topilmadi yoki Telegram javob bermadi. Birozdan keyin urinib ko'ring.",
            reply_markup=gift_shop_keyboard([], STARS_PER_DIAMOND),
        )
        return
    text = (
        "🎁 <b>Telegram sovg'alari</b>\n\n"
        f"Almazingizni Telegram sovg'asiga almashtiring.\n"
        f"Kurs: <b>1<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji> = {STARS_PER_DIAMOND}⭐</b>\n"
        "Sovg'a sizga shaxsiy chatingizga yuboriladi."
    )
    try:
        await callback.message.edit_text(text, reply_markup=gift_shop_keyboard(gifts, STARS_PER_DIAMOND))
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("gift:buy:"))
async def gift_buy_confirm(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    async with SessionLocal() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one_or_none()
        if user is None or not _user_is_vip(user):
            await callback.answer("Bu bo'lim faqat VIP User uchun. Do'kondan VIP User faollashtiring.", show_alert=True)
            return
    gift_id = callback.data.split(":", maxsplit=2)[2]
    gifts = await _fetch_sorted_gifts(callback.bot)
    target = next((g for g in gifts if getattr(g, "id", None) == gift_id), None)
    if target is None:
        await callback.answer("Bu sovg'a endi mavjud emas.", show_alert=True)
        await shop_gifts_open(callback)
        return
    stars = int(getattr(target, "star_count", 0) or 0)
    diamonds = _diamonds_for_stars(stars)
    text = (
        "🎁 <b>Sovg'ani tasdiqlang</b>\n\n"
        f"Narx: <b>{stars}⭐</b>\n"
        f"Sizdan yechiladi: <b>{diamonds}<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji></b>\n\n"
        "Tasdiqlasangiz, sovg'a darhol botning shaxsiy chatingizga yuboriladi."
    )
    try:
        await callback.message.edit_text(text, reply_markup=gift_confirm_keyboard(gift_id))
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("gift:confirm:"))
async def gift_confirm(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    gift_id = callback.data.split(":", maxsplit=2)[2]

    async with SessionLocal() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one_or_none()
        if user is None or not _user_is_vip(user):
            await callback.answer("Bu bo'lim faqat VIP User uchun. Do'kondan VIP User faollashtiring.", show_alert=True)
            return

    gifts = await _fetch_sorted_gifts(callback.bot)
    target = next((g for g in gifts if getattr(g, "id", None) == gift_id), None)
    if target is None:
        await callback.answer("Sovg'a endi mavjud emas.", show_alert=True)
        await shop_gifts_open(callback)
        return
    stars = int(getattr(target, "star_count", 0) or 0)
    diamonds_cost = _diamonds_for_stars(stars)

    # Atomically deduct diamonds.
    async with SessionLocal() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one_or_none()
        if user is None:
            await callback.answer("Avval botga /start bosing.", show_alert=True)
            return
        if (user.diamonds or 0) < diamonds_cost:
            await callback.answer(
                f"Balans yetarli emas. Kerak: 💎 {diamonds_cost}", show_alert=True,
            )
            return
        user.diamonds -= diamonds_cost
        _record_diamond_transaction(
            session,
            user,
            -diamonds_cost,
            "gift_redeem",
            note=f"Telegram sovg'a {gift_id} ({stars}⭐)",
        )
        await session.commit()

    # Try to send the gift; refund on failure.
    try:
        await callback.bot.send_gift(
            gift_id=gift_id,
            user_id=callback.from_user.id,
            text="🎁 Mafia bot sovg'asi",
        )
    except Exception as exc:
        # Refund
        async with SessionLocal() as session:
            user = (await session.execute(
                select(User).where(User.telegram_id == callback.from_user.id)
            )).scalar_one_or_none()
            if user is not None:
                user.diamonds = (user.diamonds or 0) + diamonds_cost
                _record_diamond_transaction(
                    session,
                    user,
                    diamonds_cost,
                    "gift_refund",
                    note=f"Sovg'a {gift_id} yuborilmadi: {exc}",
                )
                await session.commit()
        await callback.answer(
            "❌ Sovg'a yuborilmadi. Olmoslar qaytarildi.", show_alert=True,
        )
        try:
            await callback.message.edit_text(
                "❌ Sovg'a yuborilmadi. Olmoslar hisobingizga qaytarildi.\n"
                "Iltimos, birozdan keyin yana urinib ko'ring.",
                reply_markup=gift_shop_keyboard(await _fetch_sorted_gifts(callback.bot), STARS_PER_DIAMOND),
            )
        except TelegramBadRequest:
            pass
        return

    await callback.answer("🎁 Sovg'a yuborildi!", show_alert=True)
    try:
        await callback.message.edit_text(
            "✅ <b>Sovg'a muvaffaqiyatli yuborildi!</b>\n\n"
            f"💎 {diamonds_cost} olmos hisobingizdan yechildi.\n"
            f"Telegramdagi sovg'alaringizni tekshirib ko'ring.",
            reply_markup=gift_shop_keyboard(await _fetch_sorted_gifts(callback.bot), STARS_PER_DIAMOND),
        )
    except TelegramBadRequest:
        pass


# ===== Diamond -> Telegram Premium subscription =====
@router.callback_query(F.data == "gift:premium")
async def premium_open(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    async with SessionLocal() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one_or_none()
        if user is None or not _user_is_vip(user):
            await callback.answer("Bu bo'lim faqat VIP User uchun. Do'kondan VIP User faollashtiring.", show_alert=True)
            return
    text = (
        "👑 <b>Telegram Premium</b>\n\n"
        "Almazingizni Telegram Premium obunasiga almashtiring.\n"
        "Quyidagi rejalardan birini tanlang:"
    )
    try:
        await callback.message.edit_text(text, reply_markup=premium_shop_keyboard(PREMIUM_PLANS))
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("gift:premium:buy:"))
async def premium_buy_confirm(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    async with SessionLocal() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one_or_none()
        if user is None or not _user_is_vip(user):
            await callback.answer("Bu bo'lim faqat VIP User uchun. Do'kondan VIP User faollashtiring.", show_alert=True)
            return
    raw = callback.data.split(":", maxsplit=3)[3]
    try:
        months = int(raw)
    except ValueError:
        await callback.answer("Noto'g'ri reja.", show_alert=True)
        return
    plan = PREMIUM_BY_MONTHS.get(months)
    if plan is None:
        await callback.answer("Bunday reja topilmadi.", show_alert=True)
        return
    stars, diamonds = plan
    text = (
        "👑 <b>Premium obunani tasdiqlang</b>\n\n"
        f"Muddat: <b>{months} oy</b>\n"
        f"Narx: <b>{diamonds}<tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji></b>\n\n"
        "Tasdiqlasangiz, Premium darhol sizning hisobingizga ulanadi."
    )
    try:
        await callback.message.edit_text(text, reply_markup=premium_confirm_keyboard(months))
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("gift:premium:confirm:"))
async def premium_confirm_buy(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    raw = callback.data.split(":", maxsplit=3)[3]
    try:
        months = int(raw)
    except ValueError:
        await callback.answer("Noto'g'ri reja.", show_alert=True)
        return
    plan = PREMIUM_BY_MONTHS.get(months)
    if plan is None:
        await callback.answer("Bunday reja topilmadi.", show_alert=True)
        return
    stars, diamonds_cost = plan

    async with SessionLocal() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one_or_none()
        if user is None or not _user_is_vip(user):
            await callback.answer("Bu bo'lim faqat VIP User uchun. Do'kondan VIP User faollashtiring.", show_alert=True)
            return
        if (user.diamonds or 0) < diamonds_cost:
            await callback.answer(
                f"Balans yetarli emas. Kerak: 💎 {diamonds_cost}", show_alert=True,
            )
            return
        user.diamonds -= diamonds_cost
        _record_diamond_transaction(
            session,
            user,
            -diamonds_cost,
            "premium_redeem",
            note=f"Telegram Premium {months} oy ({stars}⭐)",
        )
        await session.commit()

    # Try to gift the premium subscription; refund on failure.
    try:
        await callback.bot.gift_premium_subscription(
            user_id=callback.from_user.id,
            month_count=months,
            star_count=stars,
            text="👑 Mafia bot sovg'asi: Telegram Premium",
        )
    except Exception as exc:
        async with SessionLocal() as session:
            user = (await session.execute(
                select(User).where(User.telegram_id == callback.from_user.id)
            )).scalar_one_or_none()
            if user is not None:
                user.diamonds = (user.diamonds or 0) + diamonds_cost
                _record_diamond_transaction(
                    session,
                    user,
                    diamonds_cost,
                    "premium_refund",
                    note=f"Premium {months} oy yuborilmadi: {exc}",
                )
                await session.commit()
        await callback.answer(
            "❌ Premium yuborilmadi. Olmoslar qaytarildi.", show_alert=True,
        )
        try:
            await callback.message.edit_text(
                "❌ <b>Premium yuborilmadi.</b>\n\n"
                "Olmoslar hisobingizga qaytarildi.\n"
                "Iltimos, birozdan keyin yana urinib ko'ring.",
                reply_markup=premium_shop_keyboard(PREMIUM_PLANS),
            )
        except TelegramBadRequest:
            pass
        return

    await callback.answer("👑 Premium ulandi!", show_alert=True)
    try:
        await callback.message.edit_text(
            "✅ <b>Telegram Premium muvaffaqiyatli ulandi!</b>\n\n"
            f"Muddat: <b>{months} oy</b>\n"
            f"💎 {diamonds_cost} olmos hisobingizdan yechildi.",
            reply_markup=premium_shop_keyboard(PREMIUM_PLANS),
        )
    except TelegramBadRequest:
        pass


# ===== VIP User =====
def _user_is_vip(user: User) -> bool:
    return _vip_expires_after(user.vip_until, _utc_now())


@router.callback_query(F.data == "vip:open")
async def vip_open(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    text = (
        "👑 <b>VIP User</b>\n\n"
        "VIP User faollashtirish orqali siz:\n"
        "• Telegram sovg'alarini sotib olish\n"
        "• Telegram Premium sotib olish\n"
        "imkoniyatiga ega bo'lasiz.\n\n"
        "Muddat: <b>30 kun</b>\n"
        "Narx: <b>30💎</b> yoki <b>190⭐</b>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=vip_keyboard())
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "vip:buy:diamonds")
async def vip_buy_diamonds(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    async with SessionLocal() as session:
        user = (await session.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )).scalar_one_or_none()
        if user is None:
            await callback.answer("Avval /start bosing.", show_alert=True)
            return
        if (user.diamonds or 0) < 30:
            await callback.answer("Balans yetarli emas. Kerak: 💎 30", show_alert=True)
            return
        user.diamonds -= 30
        now = _utc_now()
        current_vip_until = _as_aware_utc(user.vip_until)
        if current_vip_until and current_vip_until > now:
            user.vip_until = current_vip_until + timedelta(days=30)
        else:
            user.vip_until = now + timedelta(days=30)
        _record_diamond_transaction(
            session,
            user,
            -30,
            "vip_activation",
            note="VIP User 30 kun",
        )
        await session.commit()
    await callback.answer("✅ VIP User faollashtirildi!", show_alert=True)


@router.callback_query(F.data == "vip:buy:stars")
async def vip_buy_stars(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    try:
        await callback.bot.send_invoice(
            chat_id=callback.from_user.id,
            title="👑 VIP User",
            description="VIP User 30 kunlik faollashtirish",
            payload=f"vip:{callback.from_user.id}",
            currency="XTR",
            prices=[LabeledPrice(label="👑 VIP User 30 kun", amount=190)],
            provider_token="",
        )
        await callback.answer("Invoice jo'natildi!")
    except Exception as e:
        await callback.answer(f"Invoice yaratilmadi: {str(e)}", show_alert=True)
