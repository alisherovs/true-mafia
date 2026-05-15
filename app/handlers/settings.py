from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.game_engine import GameEngine
from app.keyboards import group_welcome_keyboard, settings_keyboard
from app.texts import t
from app.roles import role_preset_label, role_preset_max_players

router = Router()
PENDING_GROUP_WELCOME_ACTIONS: dict[int, dict[str, int | str]] = {}


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


@router.message(Command("settings"))
async def cmd_settings(message: Message, engine: GameEngine) -> None:
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "group_only"))
        return

    lang = await engine.get_group_language(message.chat.id)
    allowed = await engine.is_admin_or_creator(message.bot, message.chat.id, message.from_user.id)
    if not allowed:
        await message.reply(t(lang, "no_permission"))
        return

    group = await engine.group_settings(message.chat.id)
    welcome = await engine.welcome_settings(message.chat.id)
    welcome_status = "🟢 yoqilgan" if welcome["enabled"] == "1" else "🔴 o'chirilgan"
    text = (
        f"{t(lang, 'settings_title')}\n\n"
        f"⏳ Registration timeout: <b>{group.registration_timeout}</b> soniya\n"
        f"🌙 Night timeout: <b>{group.night_timeout}</b> soniya\n"
        f"☀️ Day discussion timeout: <b>{group.day_discussion_timeout}</b> soniya\n"
        f"🗳 Voting timeout: <b>{group.day_voting_timeout}</b> soniya\n"
        f"👥 Minimum players: <b>{group.min_players}</b>\n\n"
        f"👋 Salomlashuv: <b>{welcome_status}</b>\n\n"
        f"🎭 Role preset: <b>{role_preset_label(group.role_preset)}</b> "
        f"({role_preset_max_players(group.role_preset)} gacha)\n\n"
        "Buyruqlar:\n"
        "/settimeout 150\n"
        "/setnight 60\n"
        "/setdiscussion 45\n"
        "/setvoting 60\n"
        "/setminplayers 4"
    )
    try:
        await message.bot.send_message(
            message.from_user.id,
            text,
            reply_markup=settings_keyboard(lang, message.chat.id),
        )
        await message.reply("⚙️ Sozlamalar bot private chatiga yuborildi.")
    except TelegramForbiddenError:
        await message.reply("⚠️ Sozlamalarni botda ochish uchun avval botga /start bosing.")


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


@router.message(Command("settimeout"))
async def cmd_settimeout(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "group_only"))
        return

    lang = await engine.get_group_language(message.chat.id)
    allowed = await engine.is_admin_or_creator(message.bot, message.chat.id, message.from_user.id)
    if not allowed:
        await message.reply(t(lang, "no_permission"))
        return

    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.reply("Foydalanish: /settimeout <sekund>\nMasalan: /settimeout 180")
        return

    seconds = int(arg)
    if seconds < 10:
        await message.reply("Minimal vaqt: 10 soniya.")
        return

    ok, msg = await engine.update_group_setting(message.chat.id, "registration_timeout", seconds)
    await message.reply(msg)


async def _set_group_seconds(message: Message, command: CommandObject, engine: GameEngine, field: str, usage: str) -> None:
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "group_only"))
        return

    lang = await engine.get_group_language(message.chat.id)
    allowed = await engine.is_admin_or_creator(message.bot, message.chat.id, message.from_user.id)
    if not allowed:
        await message.reply(t(lang, "no_permission"))
        return

    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.reply(usage)
        return

    seconds = int(arg)
    if seconds < 10:
        await message.reply("Minimal vaqt: 10 soniya.")
        return

    ok, msg = await engine.update_group_setting(message.chat.id, field, seconds)
    await message.reply(msg)


@router.message(Command("setnight"))
async def cmd_setnight(message: Message, command: CommandObject, engine: GameEngine) -> None:
    await _set_group_seconds(message, command, engine, "night_timeout", "Foydalanish: /setnight <sekund>\nMasalan: /setnight 60")


@router.message(Command("setdiscussion"))
async def cmd_setdiscussion(message: Message, command: CommandObject, engine: GameEngine) -> None:
    await _set_group_seconds(
        message,
        command,
        engine,
        "day_discussion_timeout",
        "Foydalanish: /setdiscussion <sekund>\nMasalan: /setdiscussion 45",
    )


@router.message(Command("setvoting"))
async def cmd_setvoting(message: Message, command: CommandObject, engine: GameEngine) -> None:
    await _set_group_seconds(message, command, engine, "day_voting_timeout", "Foydalanish: /setvoting <sekund>\nMasalan: /setvoting 60")


@router.message(Command("setminplayers"))
async def cmd_setminplayers(message: Message, command: CommandObject, engine: GameEngine) -> None:
    if message.chat.type == "private":
        lang = await engine.get_user_language(message.from_user.id)
        await message.answer(t(lang, "group_only"))
        return

    lang = await engine.get_group_language(message.chat.id)
    allowed = await engine.is_admin_or_creator(message.bot, message.chat.id, message.from_user.id)
    if not allowed:
        await message.reply(t(lang, "no_permission"))
        return

    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.reply("Foydalanish: /setminplayers <son>\nMasalan: /setminplayers 6")
        return

    ok, msg = await engine.update_group_setting(message.chat.id, "min_players", int(arg))
    await message.reply(msg)
