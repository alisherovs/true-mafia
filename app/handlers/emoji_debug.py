from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message

from app.config import Settings


router = Router(name="emoji_debug")


class HasCustomEmojiFilter(BaseFilter):
    """Faqat custom_emoji entity'si bor xabarlar uchun true qaytaradi."""

    async def __call__(self, message: Message) -> bool:
        entities = list(message.entities or []) + list(message.caption_entities or [])
        return any(ent.type == "custom_emoji" and ent.custom_emoji_id for ent in entities)


def _is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


@router.message(Command("emoji_debug"), F.chat.type == "private")
async def emoji_debug(message: Message, settings: Settings) -> None:
    """Extract custom emoji IDs from a replied/forwarded message.

    Usage:
        1. Forward a message containing premium custom emojis from another chat
           into the bot's private chat.
        2. Reply to that forwarded message with /emoji_debug.
        3. The bot will list every custom emoji entity (id + fallback char).
    """
    if message.from_user is None or not _is_admin(message.from_user.id, settings):
        return

    target = message.reply_to_message
    if target is None:
        await message.reply(
            "ℹ️ Bu buyruqni custom emoji'lar bo'lgan xabarga *reply* qilib yuboring.\n"
            "Avval boshqa botdan xabarni o'z botingizga forward qiling."
        )
        return

    text = target.text or target.caption or ""
    entities = list(target.entities or []) + list(target.caption_entities or [])

    custom_emoji_entries: list[tuple[str, str]] = []
    for ent in entities:
        if ent.type != "custom_emoji" or not ent.custom_emoji_id:
            continue
        try:
            fallback = text[ent.offset : ent.offset + ent.length]
        except Exception:
            fallback = "?"
        custom_emoji_entries.append((ent.custom_emoji_id, fallback))

    if not custom_emoji_entries:
        await message.reply(
            "❌ Bu xabarda custom emoji topilmadi.\n"
            "Eslatma: forward original entitylar bilan qilinishi kerak."
        )
        return

    lines = ["🔎 <b>Topilgan custom emojilar:</b>", ""]
    seen: set[str] = set()
    for emoji_id, fallback in custom_emoji_entries:
        if emoji_id in seen:
            continue
        seen.add(emoji_id)
        lines.append(f"{fallback}  →  <code>{emoji_id}</code>")

    lines.append("")
    lines.append("📋 Kodda ishlatish uchun:")
    lines.append(
        "<pre>&lt;tg-emoji emoji-id=\"EMOJI_ID\"&gt;EMOJI&lt;/tg-emoji&gt;</pre>"
    )

    await message.reply("\n".join(lines))


@router.message(F.chat.type == "private", HasCustomEmojiFilter())
async def auto_extract_custom_emojis(message: Message) -> None:
    """Avtomatik: private chat'da custom emoji bo'lgan istalgan xabar uchun ID'ni qaytaradi."""
    text = message.text or message.caption or ""
    entities = list(message.entities or []) + list(message.caption_entities or [])

    custom_emoji_entries: list[tuple[str, str]] = []
    for ent in entities:
        if ent.type != "custom_emoji" or not ent.custom_emoji_id:
            continue
        try:
            fallback = text[ent.offset : ent.offset + ent.length]
        except Exception:
            fallback = "?"
        custom_emoji_entries.append((ent.custom_emoji_id, fallback))

    if not custom_emoji_entries:
        return

    lines = ["🔎 <b>Custom emoji ID'lari:</b>", ""]
    seen: set[str] = set()
    for emoji_id, fallback in custom_emoji_entries:
        if emoji_id in seen:
            continue
        seen.add(emoji_id)
        lines.append(f"{fallback}  →  <code>{emoji_id}</code>")

    await message.reply("\n".join(lines))
