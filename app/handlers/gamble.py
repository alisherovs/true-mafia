from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.database import SessionLocal
from app.frog_road import (
    FROG_MAX_BET,
    FROG_MIN_BET,
    FrogRoadEngine,
    FrogView,
    build_frog_start_keyboard,
    frog_start_text,
    parse_frog_callback,
)
from app.game_engine import GAMBLE_GROUP_PAY_DAYS, GAMBLE_GROUP_WEEK_PRICE_DIAMONDS, GameEngine
from app.gamble_mines import MinesAntiCheatValidator, MinesEngine, MinesView

router = Router()


class FrogBetState(StatesGroup):
    waiting_amount = State()


def _gamble_group_redirect_text(link: str, pay_chat_id: int | None = None) -> str:
    price = GAMBLE_GROUP_WEEK_PRICE_DIAMONDS
    days = GAMBLE_GROUP_PAY_DAYS
    if pay_chat_id is not None and pay_chat_id < 0:
        text = (
            "🎰 <b>Qimor bu guruhda hali ochilmagan</b>\n\n"
            f"Bu guruhda qimorni ochish uchun <b>{price}</b> 💎 to'lov qiling.\n"
            f"Amal qilish muddati: <b>{days} kun</b>."
        )
        if link:
            text += "\n\n✨ Yoki siz maxsus qimor guruhida o'ynashingiz mumkin."
        return text
    return "🎰 <b>Qimor bu guruhda o'chirilgan.</b>"


def _gamble_group_keyboard(link: str, pay_chat_id: int | None = None) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    if pay_chat_id is not None and pay_chat_id < 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💎 {GAMBLE_GROUP_WEEK_PRICE_DIAMONDS} olmosga {GAMBLE_GROUP_PAY_DAYS} kunga ochish",
                    callback_data=f"gpay:{pay_chat_id}",
                )
            ]
        )
    if link:
        rows.append(
            [InlineKeyboardButton(text="🎰 Maxsus qimor guruhiga o'tish", url=link)]
        )
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def _parse_frog_bet(raw: str | None) -> tuple[bool, int, str]:
    try:
        amount = int((raw or "").strip().split()[0])
    except (AttributeError, IndexError, ValueError):
        return False, 0, "❌ To'g'ri summa kiriting. Masalan: 1000"
    if amount < FROG_MIN_BET or amount > FROG_MAX_BET:
        return False, 0, f"❌ Stavka <b>{FROG_MIN_BET}</b> dan <b>{FROG_MAX_BET}</b> coin gacha bo'lishi kerak."
    return True, amount, ""


async def _edit_or_answer(message: Message, view: FrogView) -> None:
    try:
        await message.edit_text(view.text, reply_markup=view.keyboard)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await message.answer(view.text, reply_markup=view.keyboard)


async def _gamble_allowed_or_reply(message: Message, engine: GameEngine) -> bool:
    if message.from_user is None:
        return False
    if not await engine.is_gamble_enabled():
        await message.answer("🚫 Bu xizmat vaqtinchalik ishlamaydi. Admin tomonidan cheklangan.")
        return False
    allowed, link = await engine.gamble_chat_check(message.chat.id)
    if not allowed:
        await message.answer(
            _gamble_group_redirect_text(link, pay_chat_id=message.chat.id),
            reply_markup=_gamble_group_keyboard(link, pay_chat_id=message.chat.id),
        )
        return False
    return True


@router.message(Command("qimor", "frog"))
async def cmd_qimor(message: Message, command: CommandObject, engine: GameEngine, state: FSMContext) -> None:
    if not await _gamble_allowed_or_reply(message, engine):
        return
    await state.clear()
    frog = FrogRoadEngine(SessionLocal)
    if command.args:
        ok, amount, error = _parse_frog_bet(command.args)
        if not ok:
            await message.answer(error, reply_markup=build_frog_start_keyboard())
            return
        view = await frog.start_frog_game(message.from_user, message.chat.id, amount)
        sent = await message.answer(view.text, reply_markup=view.keyboard)
        if view.session_id:
            await frog.set_message_id(view.session_id, sent.message_id)
        return
    await message.answer(frog_start_text(), reply_markup=build_frog_start_keyboard())


@router.message(FrogBetState.waiting_amount)
async def frog_custom_bet_amount(message: Message, state: FSMContext, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if not await _gamble_allowed_or_reply(message, engine):
        await state.clear()
        return
    ok, amount, error = _parse_frog_bet(message.text)
    if not ok:
        await message.answer(error or "❌ To'g'ri summa kiriting. Masalan: 1000")
        return
    await state.clear()
    frog = FrogRoadEngine(SessionLocal)
    view = await frog.start_frog_game(message.from_user, message.chat.id, amount)
    sent = await message.answer(view.text, reply_markup=view.keyboard)
    if view.session_id:
        await frog.set_message_id(view.session_id, sent.message_id)


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


@router.callback_query(F.data.startswith("frog:"))
async def frog_callback(callback: CallbackQuery, engine: GameEngine, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await engine.is_gamble_enabled():
        await callback.answer("Bu xizmat vaqtinchalik ishlamaydi. Admin tomonidan cheklangan.", show_alert=True)
        return
    allowed, _link = await engine.gamble_chat_check(callback.message.chat.id)
    if not allowed:
        await callback.answer("🎰 Qimor bu guruhda ochilmagan. /qimor yozib to'lov qiling.", show_alert=True)
        return
    if callback.data == "frog:menu_cancel":
        await state.clear()
        try:
            await callback.message.edit_text("❌ Qurbaqa Yo'li menyusi bekor qilindi.")
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.message.answer("❌ Qurbaqa Yo'li menyusi bekor qilindi.")
        await callback.answer("Bekor qilindi.")
        return

    try:
        action, value, column = parse_frog_callback(callback.data or "")
    except ValueError:
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return

    frog = FrogRoadEngine(SessionLocal)
    if action == "custom_bet":
        await state.set_state(FrogBetState.waiting_amount)
        await callback.message.answer(
            f"✍️ Stavka summasini kiriting.\nMinimal: <b>{FROG_MIN_BET}</b> coin\nMaksimal: <b>{FROG_MAX_BET}</b> coin"
        )
        await callback.answer("Summani yozing.")
        return
    if action == "start":
        view = await frog.start_frog_game(callback.from_user, callback.message.chat.id, int(value or 0))
        await _edit_or_answer(callback.message, view)
        if view.session_id:
            await frog.set_message_id(view.session_id, callback.message.message_id)
        await callback.answer(view.alert or "O'yin boshlandi.", show_alert=view.show_alert)
        return
    if action == "jump":
        if value is None or column is None:
            await callback.answer("Callback noto'g'ri.", show_alert=True)
            return
        view = await frog.handle_frog_jump(callback.from_user.id, value, column)
    elif action == "cashout":
        if value is None:
            await callback.answer("Callback noto'g'ri.", show_alert=True)
            return
        view = await frog.handle_frog_cashout(callback.from_user.id, value)
    elif action == "cancel":
        if value is None:
            await callback.answer("Callback noto'g'ri.", show_alert=True)
            return
        view = await frog.handle_frog_cancel(callback.from_user.id, value)
    else:
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return

    if view.text:
        await _edit_or_answer(callback.message, view)
    await callback.answer(view.alert or "OK", show_alert=view.show_alert)


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
