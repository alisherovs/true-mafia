from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import Settings
from app.game_engine import GameEngine
from app.keyboards import profile_dashboard_keyboard

router = Router()


@router.message(Command("profile"))
async def cmd_profile(message: Message, engine: GameEngine, settings: Settings) -> None:
    await engine.ensure_user(message.from_user)
    user = await engine.get_user(message.from_user.id)
    if user is None:
        return

    if await engine.user_in_running_game(message.from_user.id):
        await message.answer(
            "🎮 <b>Siz hozir aktiv o'yindasiz.</b>\n\n"
            "Profil statistikasi o'yin davomida ko'rsatilmaydi."
        )
        return

    await message.answer(
        engine.format_user_dashboard(user),
        reply_markup=profile_dashboard_keyboard(settings, is_admin=message.from_user.id in settings.admin_ids),
    )
