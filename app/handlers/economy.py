from __future__ import annotations

from typing import Optional
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.database import SessionLocal
from app.game_engine import GameEngine
from app.keyboards import role_shop_keyboard, shop_keyboard
from app.models import User
from app.texts import t

router = Router()


@router.message(Command("shop"))
async def cmd_shop(message: Message, engine: GameEngine) -> None:
    await engine.ensure_user(message.from_user)
    await message.answer(
        "🛒 <b>Do'kon</b>\n\n"
        "Dollar orqali himoya va maxsus imkoniyatlar sotib olishingiz mumkin.",
        reply_markup=shop_keyboard(),
    )


@router.callback_query(F.data == "shop:open")
async def shop_open_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    await engine.ensure_user(callback.from_user)
    await callback.message.edit_text(
        "🛒 <b>Do'kon</b>\n\n"
        "Kerakli itemni tanlang. Xarid summasi profilingizdagi dollardan yechiladi.",
        reply_markup=shop_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "shop:roles")
async def shop_roles_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    await callback.message.edit_text(
        "🃏 <b>Keyingi o'yindagi rol</b>\n\n"
        "Agar tanlangan rol o'sha o'yin role jadvalida mavjud bo'lsa, bot uni sizga berishga harakat qiladi.",
        reply_markup=role_shop_keyboard(),
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


@router.callback_query(F.data.startswith("shop:role:"))
async def shop_role_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    role_key = callback.data.split(":", maxsplit=2)[2]
    ok, text = await engine.buy_shop_item(callback.from_user.id, f"role:{role_key}")
    await callback.answer(text, show_alert=True)


@router.message(Command("give"))
async def cmd_give(message: Message, engine: GameEngine) -> None:
    lang = await engine.get_user_language(message.from_user.id)
    await message.reply(t(lang, "give_started"))


@router.message(Command("giveto"))
async def cmd_giveto(message: Message, command: CommandObject, engine: GameEngine) -> None:
    sender = await engine.ensure_user(message.from_user)
    lang = sender.language

    target_id: Optional[int] = None
    amount: Optional[int] = None

    if message.reply_to_message and command.args and command.args.strip().isdigit():
        target_id = message.reply_to_message.from_user.id
        amount = int(command.args.strip())
    elif command.args:
        parts = command.args.split()
        if len(parts) >= 2 and parts[1].isdigit():
            amount = int(parts[1])
            raw_target = parts[0].strip()
            if raw_target.startswith("@"):
                username = raw_target[1:]
                async with SessionLocal() as session:
                    target = (await session.execute(select(User).where(User.username == username))).scalar_one_or_none()
                    target_id = target.telegram_id if target else None
            elif raw_target.isdigit():
                target_id = int(raw_target)

    if target_id is None or amount is None:
        await message.reply(t(lang, "give_usage"))
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

    await message.reply(t(lang, "give_success", amount=amount))
    try:
        await message.bot.send_message(target_id, t(await engine.get_user_language(target_id), "give_received", amount=amount))
    except Exception:
        pass
