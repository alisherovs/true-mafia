from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.database import SessionLocal
from app.game_engine import GameEngine
from app.gamble_mines import MinesAntiCheatValidator, MinesEngine, MinesView

router = Router()


def _gamble_group_redirect_text(link: str) -> str:
    base = (
        "🎰 <b>Qimor faqat maxsus guruhda o'ynaladi</b>\n\n"
        "Bu guruhda qimor o'chirilgan. Iltimos, rasmiy qimor guruhiga qo'shiling."
    )
    if link:
        base += f"\n\n🔗 {link}"
    return base


def _gamble_group_keyboard(link: str) -> InlineKeyboardMarkup | None:
    if not link:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎰 Qimor guruhiga o'tish", url=link)]]
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
            _gamble_group_redirect_text(link),
            reply_markup=_gamble_group_keyboard(link),
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
            _gamble_group_redirect_text(link),
            reply_markup=_gamble_group_keyboard(link),
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
    allowed, link = await engine.gamble_chat_check(callback.message.chat.id)
    if not allowed:
        await callback.answer(
            "🎰 Qimor faqat maxsus guruhda o'ynaladi." + (f"\n\n{link}" if link else ""),
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
