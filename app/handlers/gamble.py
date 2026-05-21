from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.database import SessionLocal
from app.gamble_mines import MinesAntiCheatValidator, MinesEngine

router = Router()


@router.message(Command("qimor"))
async def cmd_qimor(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    mines = MinesEngine(SessionLocal)
    view = await mines.start_or_resume(message.from_user, message.chat.id, command.args)
    sent = await message.answer(view.text, reply_markup=view.keyboard)
    if view.game_id and view.token:
        await mines.set_message_id(view.game_id, view.token, sent.message_id)


@router.message(Command("topq"))
async def cmd_topq(message: Message) -> None:
    mines = MinesEngine(SessionLocal)
    await message.answer(await mines.weekly_top_text(limit=10))


@router.callback_query(F.data.startswith("gm:"))
async def gamble_mines_callback(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
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

    if view.text:
        try:
            await callback.message.edit_text(view.text, reply_markup=view.keyboard)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.message.answer(view.text, reply_markup=view.keyboard)
    await callback.answer(view.alert or "OK", show_alert=view.show_alert)
