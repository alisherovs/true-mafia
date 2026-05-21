from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, Message

from app.game_engine import GameEngine
from app.keyboards import hero_game_keyboard, hero_list_keyboard, hero_panel_keyboard, hero_target_keyboard

router = Router()
PENDING_HERO_ACTIONS: dict[int, dict[str, object]] = {}


class PendingHeroFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return bool(message.from_user and message.chat.type == "private" and message.from_user.id in PENDING_HERO_ACTIONS)


async def _require_private(callback: CallbackQuery) -> bool:
    if callback.message and callback.message.chat.type != "private":
        await callback.answer("Geroy paneli faqat bot private chatida ochiladi.", show_alert=True)
        return False
    return True


async def _show_panel(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        return
    ok, text, is_for_sale = await engine.hero_panel_data(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=hero_panel_keyboard(is_for_sale) if ok else None)


@router.callback_query(F.data == "hero:shop:buy")
async def hero_buy_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    ok, text = await engine.buy_hero(callback.from_user.id)
    await callback.answer(text, show_alert=True)
    if ok and callback.message:
        await _show_panel(callback, engine)


@router.callback_query(F.data == "hero:panel")
async def hero_panel_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    await _show_panel(callback, engine)
    await callback.answer()


@router.callback_query(F.data == "hero:list")
async def hero_list_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    ok, text, heroes = await engine.hero_list_text(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=hero_list_keyboard(heroes) if ok else None)
    await callback.answer(text if not ok else None, show_alert=not ok)


@router.callback_query(F.data.startswith("hero:select:"))
async def hero_select_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    raw = callback.data.rsplit(":", maxsplit=1)[-1]
    if not raw.isdigit():
        await callback.answer("Bad callback", show_alert=True)
        return
    ok, text = await engine.hero_select_active(callback.from_user.id, int(raw))
    await callback.answer(text, show_alert=True)
    ok_list, list_text, heroes = await engine.hero_list_text(callback.from_user.id)
    await callback.message.edit_text(list_text, reply_markup=hero_list_keyboard(heroes) if ok_list else None)


@router.callback_query(F.data.in_({"hero:add_points", "hero:upgrade_def", "hero:recharge"}))
async def hero_purchase_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    if callback.data == "hero:add_points":
        ok, text = await engine.hero_add_points(callback.from_user.id)
    elif callback.data == "hero:upgrade_def":
        ok, text = await engine.hero_upgrade_defense(callback.from_user.id)
    else:
        ok, text = await engine.hero_recharge(callback.from_user.id)
    await callback.answer(text, show_alert=True)
    if ok and callback.message:
        await _show_panel(callback, engine)


@router.callback_query(F.data.in_({"hero:rename", "hero:sell", "hero:sale:price"}))
async def hero_pending_callback(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    action = callback.data.rsplit(":", maxsplit=1)[-1]
    if callback.data == "hero:rename":
        PENDING_HERO_ACTIONS[callback.from_user.id] = {"action": "rename"}
        await callback.message.edit_text("🖋 Yangi geroy nomini yuboring. 2-20 belgi.")
    elif callback.data == "hero:sell":
        PENDING_HERO_ACTIONS[callback.from_user.id] = {"action": "sell_price"}
        await callback.message.edit_text("🏷 Geroy sotuv narxini almazda yuboring. Masalan: <code>500</code>")
    else:
        PENDING_HERO_ACTIONS[callback.from_user.id] = {"action": "sale_price"}
        await callback.message.edit_text("✏️ Yangi sotuv narxini almazda yuboring. Masalan: <code>500</code>")
    await callback.answer()


@router.callback_query(F.data == "hero:sale:cancel")
async def hero_cancel_sale_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    ok, text = await engine.hero_cancel_sale(callback.bot, callback.from_user.id)
    await callback.answer(text, show_alert=True)
    if callback.message:
        await _show_panel(callback, engine)


@router.callback_query(F.data.startswith("hero:market:buy:"))
async def hero_market_buy_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    hero_id_raw = callback.data.rsplit(":", maxsplit=1)[-1]
    if not hero_id_raw.isdigit():
        await callback.answer("Bad callback", show_alert=True)
        return
    ok, text = await engine.hero_market_buy(callback.bot, callback.from_user.id, int(hero_id_raw))
    await callback.answer(text, show_alert=True)


@router.callback_query(F.data.in_({"hero:game:panel", "hero:game:cancel"}))
async def hero_game_panel_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    PENDING_HERO_ACTIONS.pop(callback.from_user.id, None)
    if callback.data == "hero:game:cancel":
        await callback.message.edit_text("❌ Bekor qilindi.")
        await callback.answer()
        return
    ok, text, can_attack = await engine.hero_game_panel_text(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=hero_game_keyboard(can_attack=can_attack) if ok else None)
    await callback.answer(text if not ok else None, show_alert=not ok)


@router.callback_query(F.data == "hero:game:attack")
async def hero_game_attack_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    ok, text, targets = await engine.hero_game_targets(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=hero_target_keyboard(targets) if ok else None)
    await callback.answer(text if not ok else None, show_alert=not ok)


@router.callback_query(F.data.startswith("hero:game:target:"))
async def hero_game_target_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    raw = callback.data.rsplit(":", maxsplit=1)[-1]
    if not raw.isdigit():
        await callback.answer("Bad callback", show_alert=True)
        return
    target_player_id = int(raw)
    ok, text, _ = await engine.hero_damage_prompt(callback.from_user.id, target_player_id)
    if not ok:
        await callback.answer(text, show_alert=True)
        return
    ok, result = await engine.hero_game_attack(callback.bot, callback.from_user.id, target_player_id, "auto")
    await callback.answer(result, show_alert=True)
    await callback.message.edit_text(result)


@router.callback_query(F.data == "hero:game:damage:max")
async def hero_game_damage_max_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    pending = PENDING_HERO_ACTIONS.pop(callback.from_user.id, {})
    target_player_id = int(pending.get("target_player_id") or 0)
    ok, text = await engine.hero_game_attack(callback.bot, callback.from_user.id, target_player_id, "max")
    await callback.answer(text, show_alert=True)
    if callback.message:
        await callback.message.edit_text(text)


@router.callback_query(F.data == "hero:game:defend")
async def hero_game_defend_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    ok, text = await engine.hero_game_defend(callback.from_user.id)
    await callback.message.edit_text(text)
    await callback.answer(text if not ok else None, show_alert=not ok)


@router.callback_query(F.data.startswith("hero:game:defamount:"))
async def hero_game_defamount_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    amount = callback.data.rsplit(":", maxsplit=1)[-1]
    ok, text = await engine.hero_game_defend(callback.from_user.id, amount)
    await callback.answer(text, show_alert=True)
    if callback.message:
        await callback.message.edit_text(text)


@router.callback_query(F.data == "hero:game:hp")
async def hero_game_hp_callback(callback: CallbackQuery, engine: GameEngine) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not await _require_private(callback):
        return
    ok, text = await engine.hero_game_hp_text(callback.from_user.id)
    can_attack = False
    if ok:
        panel_ok, _, can_attack = await engine.hero_game_panel_text(callback.from_user.id)
        can_attack = panel_ok and can_attack
    await callback.message.edit_text(text, reply_markup=hero_game_keyboard(can_attack=can_attack) if ok else None)
    await callback.answer(text if not ok else None, show_alert=not ok)


@router.message(Command("geroyinfo"))
async def hero_info_command(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type == "private":
        await message.answer("Bu buyruq faqat guruhda adminlar uchun ishlaydi.")
        return
    if not await engine.is_admin_or_creator(message.bot, message.chat.id, message.from_user.id):
        await message.reply("❌ Bu buyruq faqat guruh adminlari uchun.")
        return
    if message.reply_to_message is None or message.reply_to_message.from_user is None:
        await message.reply("🥷 Qaysi user geroyini tekshirish kerak bo'lsa, o'sha xabarga reply qilib /geroyinfo yozing.")
        return
    target = message.reply_to_message.from_user
    if target.is_bot:
        await message.reply("Botlarda geroy bo'lmaydi.")
        return
    _, text = await engine.admin_hero_info_text(target.id)
    await message.reply(text)


@router.message(Command("hidegeroy"))
async def hero_hide_command(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type != "private":
        return
    text = await engine.set_hero_info_hidden(message.from_user.id, True)
    await message.answer(text)


@router.message(Command("opengeroy"))
async def hero_open_command(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if message.chat.type != "private":
        return
    text = await engine.set_hero_info_hidden(message.from_user.id, False)
    await message.answer(text)


@router.message(Command("tgeroy"))
async def hero_transfer_command(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("Bu buyruqni geroy sovg'a qilinadigan user xabariga reply qilib yuboring.")
        return
    target = message.reply_to_message.from_user
    if target.is_bot:
        await message.reply("Botga geroy sovg'a qilib bo'lmaydi.")
        return
    ok, text = await engine.transfer_active_hero(message.bot, message.from_user.id, target)
    await message.reply(text)


@router.message(PendingHeroFilter())
async def hero_pending_message(message: Message, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    pending = PENDING_HERO_ACTIONS.pop(message.from_user.id, {})
    action = pending.get("action")
    text = (message.text or "").strip()
    if action == "rename":
        ok, response = await engine.hero_rename(message.from_user.id, text)
    elif action == "sell_price":
        if not text.isdigit():
            PENDING_HERO_ACTIONS[message.from_user.id] = pending
            await message.answer("Narx faqat butun son bo'lishi kerak.")
            return
        ok, response = await engine.hero_put_for_sale(message.bot, message.from_user.id, int(text))
    elif action == "sale_price":
        if not text.isdigit():
            PENDING_HERO_ACTIONS[message.from_user.id] = pending
            await message.answer("Narx faqat butun son bo'lishi kerak.")
            return
        ok, response = await engine.hero_update_sale_price(message.bot, message.from_user.id, int(text))
    elif action == "damage":
        target_player_id = int(pending.get("target_player_id") or 0)
        ok, response = await engine.hero_game_attack(message.bot, message.from_user.id, target_player_id, text)
    else:
        return
    if not ok and action in {"rename", "sell_price", "sale_price", "damage"}:
        PENDING_HERO_ACTIONS[message.from_user.id] = pending
    await message.answer(response)
