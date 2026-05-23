from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.database import SessionLocal
from app.game_engine import GAMBLE_GROUP_PAY_DAYS, GAMBLE_GROUP_WEEK_PRICE_DIAMONDS, GameEngine
from app.gamble_mines import MinesAntiCheatValidator, MinesEngine, MinesView

router = Router()


def _gamble_group_redirect_text(link: str, pay_chat_id: int | None = None) -> str:
    price = GAMBLE_GROUP_WEEK_PRICE_DIAMONDS
    days = GAMBLE_GROUP_PAY_DAYS
    if pay_chat_id is not None and pay_chat_id < 0:
        return (
            "🎰 <b>Qimor bu guruhda hali ochilmagan</b>\n\n"
            f"Bu guruhda qimorni ochish uchun <b>{price}</b> 💎 to'lov qiling.\n"
            f"Amal qilish muddati: <b>{days} kun</b>."
        )
    return "🎰 <b>Qimor bu guruhda o'chirilgan.</b>"


def _gamble_group_keyboard(link: str, pay_chat_id: int | None = None) -> InlineKeyboardMarkup | None:
    if pay_chat_id is None or pay_chat_id >= 0:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💎 {GAMBLE_GROUP_WEEK_PRICE_DIAMONDS} olmosga {GAMBLE_GROUP_PAY_DAYS} kunga ochish",
                    callback_data=f"gpay:{pay_chat_id}",
                )
            ]
        ]
    )


async def _voice_file_id_for_view(engine: GameEngine, view: MinesView) -> str:
    if view.win_voice:
        return await engine.get_gamble_win_voice_file_id()
    if view.loss_voice:
        return await engine.get_gamble_loss_voice_file_id()
    return ""


async def _send_voice_result(message: Message, file_id: str, caption: str) -> bool:
    if not file_id:
        return False
    try:
        await message.bot.send_voice(message.chat.id, file_id, caption=caption)
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


@router.message(Command("qimor"))
async def cmd_qimor(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if not await engine.is_gamble_enabled():
        await message.answer("🚫 Bu xizmat vaqtinchalik ishlamaydi. Admin tomonidan cheklangan.")
        return
    allowed, link = await engine.gamble_chat_check(message.chat.id)
    if not allowed:
        await message.answer(
            _gamble_group_redirect_text(link, pay_chat_id=message.chat.id),
            reply_markup=_gamble_group_keyboard(link, pay_chat_id=message.chat.id),
        )
        return
    mines = MinesEngine(SessionLocal)
    view = await mines.start_or_resume(message.from_user, message.chat.id, command.args)
    file_id = await _voice_file_id_for_view(engine, view)
    if file_id and await _send_voice_result(message, file_id, view.text):
        return
    sent = await message.answer(view.text, reply_markup=view.keyboard)
    if view.game_id and view.token:
        await mines.set_message_id(view.game_id, view.token, sent.message_id)


@router.message(Command("topq"))
async def cmd_topq(message: Message, engine: GameEngine) -> None:
    allowed, link = await engine.gamble_chat_check(message.chat.id)
    if not allowed:
        await message.answer(
            _gamble_group_redirect_text(link, pay_chat_id=message.chat.id),
            reply_markup=_gamble_group_keyboard(link, pay_chat_id=message.chat.id),
        )
        return
    mines = MinesEngine(SessionLocal)
    await message.answer(await mines.weekly_top_text(limit=10))


@router.callback_query(F.data.startswith("gm:"))
async def gamble_mines_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await engine.is_gamble_enabled():
        await callback.answer("Bu xizmat vaqtinchalik ishlamaydi. Admin tomonidan cheklangan.", show_alert=True)
        return
    allowed, _link = await engine.gamble_chat_check(callback.message.chat.id)
    if not allowed:
        await callback.answer(
            "🎰 Qimor bu guruhda ochilmagan. /qimor yozib to'lov qiling.",
            show_alert=True,
        )
        return
    try:
        action, game_id, token, cell = MinesAntiCheatValidator.decode_callback(callback.data or "")
    except (TypeError, ValueError):
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return

    if action == "noop":
        await callback.answer()
        return

    mines = MinesEngine(SessionLocal)
    if action in {"j", "join"}:
        view = await mines.join(callback.from_user, game_id, token)
    elif action in {"s", "solo", "start", "start_solo"}:
        view = await mines.start_solo(callback.from_user.id, game_id, token)
    elif action in {"o", "p", "open"}:
        if cell is None:
            await callback.answer("Katak noto'g'ri.", show_alert=True)
            return
        view = await mines.open_cell(callback.from_user.id, game_id, token, cell)
    elif action in {"c", "cashout"}:
        view = await mines.cashout(callback.from_user.id, game_id, token)
    else:
        await callback.answer("Bu tugma eskirgan. /qimor bilan qayta oching.", show_alert=True)
        return

    voice_file_id = await _voice_file_id_for_view(engine, view)
    if view.text and voice_file_id:
        sent = await _send_voice_result(callback.message, voice_file_id, view.text)
        if sent:
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except TelegramBadRequest:
                    pass
            await callback.answer(view.alert or "OK", show_alert=view.show_alert)
            return

    if view.text:
        try:
            await callback.message.edit_text(view.text, reply_markup=view.keyboard)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.message.answer(view.text, reply_markup=view.keyboard)
    await callback.answer(view.alert or "OK", show_alert=view.show_alert)


@router.callback_query(F.data.startswith("gpay:"))
async def gamble_pay_group_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    parts = (callback.data or "").split(":", 1)
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return
    target_chat_id = int(parts[1])
    if target_chat_id != callback.message.chat.id:
        await callback.answer("Bu tugma boshqa guruh uchun.", show_alert=True)
        return
    if target_chat_id >= 0:
        await callback.answer("Bu xususiyat faqat guruhlar uchun.", show_alert=True)
        return
    if not await engine.is_gamble_enabled():
        await callback.answer("Qimor vaqtinchalik ishlamaydi.", show_alert=True)
        return
    if await engine.is_gamble_paid_group(target_chat_id):
        until = await engine.get_gamble_paid_until(target_chat_id)
        until_str = until.strftime("%Y-%m-%d %H:%M UTC") if until else ""
        await callback.answer(
            f"Bu guruh allaqachon ochiq. Amal qiladi: {until_str}\n/qimor yozing.",
            show_alert=True,
        )
        return
    chat_title = callback.message.chat.title or ""
    ok, status, until = await engine.pay_for_gamble_group(
        callback.from_user.id, target_chat_id, chat_title=chat_title
    )
    if not ok:
        await callback.answer(status, show_alert=True)
        return
    until_str = until.strftime("%Y-%m-%d %H:%M UTC") if until else ""
    text = (
        "✅ <b>To'lov qabul qilindi</b>\n\n"
        f"💎 Yechildi: <b>{GAMBLE_GROUP_WEEK_PRICE_DIAMONDS}</b>\n"
        f"⏳ Amal qiladi: <b>{until_str}</b>\n\n"
        "Endi /qimor yozib boshlashingiz mumkin."
    )
    try:
        await callback.message.edit_text(text)
    except TelegramBadRequest:
        await callback.message.answer(text)
    await callback.answer("✅ To'lov qabul qilindi", show_alert=True)
