from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, or_, select

from app.chicken_road import (
    CHICKEN_MAX_BET,
    CHICKEN_MIN_BET,
    ChickenRoadEngine,
    build_chicken_start_keyboard,
    chicken_start_text,
    parse_chicken_callback,
)
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
from app.gamble_guard import enforce_gamble_overwin_guard
from app.gamble_mines import MinesAntiCheatValidator, MinesEngine, MinesView
from app.models import GambleMinesGame, GameHistory, User
from app.roulette import (
    ROULETTE_MAX_BET,
    ROULETTE_MIN_BET,
    RouletteEngine,
    parse_roulette_callback,
    roulette_bet_keyboard,
    roulette_color_keyboard,
    roulette_color_text,
    roulette_start_text,
)
from app.treasure_hunt import (
    TreasureHuntEngine,
    build_treasure_start_keyboard,
    parse_treasure_callback,
    parse_treasure_new_callback,
    treasure_start_text,
)

router = Router()
ROULETTE_ENABLED = False
CHICKEN_ROAD_ENABLED = False
DAILY_GAMBLE_WIN_LIMIT = 30_000
UZ_TZ = timezone(timedelta(hours=5))


def _button(text: str, callback_data: str, style: str = "primary") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data, **{"style": style})


class FrogBetState(StatesGroup):
    waiting_amount = State()


class MinesBetState(StatesGroup):
    waiting_amount = State()


class RouletteBetState(StatesGroup):
    waiting_amount = State()


class ChickenBetState(StatesGroup):
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


def _gamble_menu_text() -> str:
    return (
        "🎰 <b>Qimor o'yinlari</b>\n\n"
        "O'ynamoqchi bo'lgan mini-o'yinni tanlang:\n\n"
        "🐸 <b>Qurbaqa Yo'li</b> - 5x8 yo'lda xavfli kataklardan qoching.\n"
        "💣 <b>Mines</b> - klassik mines va 2 kishilik qimor.\n"
        "💎 <b>Treasure Hunt</b> - 2-10 kishilik survival xazina o'yini."
    )


def _gamble_menu_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("🐸 Qurbaqa Yo'li", f"qmenu:frog:{owner_id}", "success")],
            [_button("💣 Mines", f"qmenu:mines:{owner_id}", "primary")],
            [_button("💎 Treasure Hunt", f"qmenu:treasure:{owner_id}", "success")],
            [_button("❌ Bekor qilish", f"qmenu:cancel:{owner_id}", "danger")],
        ]
    )


def _disabled_game_text(game_name: str) -> str:
    return f"⏸ <b>{game_name}</b> vaqtincha faolsizlantirilgan."


def _daily_limit_text(total: int) -> str:
    return (
        "🚫 <b>Kunlik qimor yutuq limiti tugadi.</b>\n\n"
        f"Bugungi yutuq: <b>{int(total)}</b> dollar\n"
        f"Kunlik limit: <b>{DAILY_GAMBLE_WIN_LIMIT}</b> dollar\n\n"
        "⏳ Ertaga yana o'ynashingiz mumkin."
    )


def _daily_window_start_utc() -> datetime:
    now_local = datetime.now(UZ_TZ)
    start_local = datetime.combine(now_local.date(), time.min, tzinfo=UZ_TZ)
    return start_local.astimezone(timezone.utc)


async def _daily_gamble_winnings(user_telegram_id: int) -> int:
    start_utc = _daily_window_start_utc()
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == int(user_telegram_id)))
        ).scalar_one_or_none()
        history_total = 0
        if user is not None:
            history_total = int(
                await session.scalar(
                    select(func.coalesce(func.sum(GameHistory.win_amount), 0)).where(
                        GameHistory.user_id == int(user.id),
                        GameHistory.win_amount > 0,
                        GameHistory.created_at >= start_utc,
                    )
                )
                or 0
            )
        mines_total = int(
            await session.scalar(
                select(func.coalesce(func.sum(GambleMinesGame.payout), 0)).where(
                    GambleMinesGame.status == "cashed",
                    GambleMinesGame.payout > 0,
                    GambleMinesGame.ended_at.is_not(None),
                    GambleMinesGame.ended_at >= start_utc,
                    or_(
                        GambleMinesGame.winner_telegram_id == int(user_telegram_id),
                        (
                            GambleMinesGame.winner_telegram_id.is_(None)
                            & (GambleMinesGame.user_telegram_id == int(user_telegram_id))
                        ),
                    ),
                )
            )
            or 0
        )
    return history_total + mines_total


async def _daily_gamble_limit_reached(user_telegram_id: int) -> tuple[bool, int]:
    total = await _daily_gamble_winnings(user_telegram_id)
    return total >= DAILY_GAMBLE_WIN_LIMIT, total


async def _check_daily_limit_message(message: Message) -> bool:
    if message.from_user is None:
        return False
    limited, total = await _daily_gamble_limit_reached(message.from_user.id)
    if limited:
        await message.answer(_daily_limit_text(total))
        return True
    return False


async def _check_daily_limit_callback(callback: CallbackQuery) -> bool:
    if callback.from_user is None:
        return False
    limited, total = await _daily_gamble_limit_reached(callback.from_user.id)
    if limited:
        await callback.answer(
            f"Kunlik limit tugadi. Bugungi yutuq: {int(total)} / {DAILY_GAMBLE_WIN_LIMIT} dollar.",
            show_alert=True,
        )
        return True
    return False


async def _enforce_overwin_for_user(callback: CallbackQuery, telegram_id: int | None = None) -> None:
    target_id = int(telegram_id or callback.from_user.id)
    await enforce_gamble_overwin_guard(SessionLocal, callback.bot, target_id)


async def _enforce_overwin_for_mines_game(callback: CallbackQuery, game_id: int) -> None:
    ids: set[int] = {int(callback.from_user.id)}
    async with SessionLocal() as session:
        game = await session.get(GambleMinesGame, int(game_id))
        if game is not None:
            ids.add(int(game.user_telegram_id))
            if game.opponent_telegram_id:
                ids.add(int(game.opponent_telegram_id))
            if game.winner_telegram_id:
                ids.add(int(game.winner_telegram_id))
    for user_id in ids:
        await enforce_gamble_overwin_guard(SessionLocal, callback.bot, user_id)


def _mines_start_text() -> str:
    return (
        "💣 <b>Mines</b>\n\n"
        "2 kishilik qimor yaratishingiz yoki pastdagi tugma orqali 1 kishilik mines boshlashingiz mumkin.\n\n"
        "Stavkani tanlang:"
    )


def _mines_bet_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("100", f"gmnew:start:{owner_id}:100", "primary"),
                _button("500", f"gmnew:start:{owner_id}:500", "primary"),
                _button("1000", f"gmnew:start:{owner_id}:1000", "primary"),
            ],
            [
                _button("5000", f"gmnew:start:{owner_id}:5000", "primary"),
                _button("10000", f"gmnew:start:{owner_id}:10000", "primary"),
            ],
            [_button("✍️ Boshqa summa", f"gmnew:custom:{owner_id}", "success")],
            [_button("⬅️ Ortga", f"qmenu:back:{owner_id}", "danger")],
        ]
    )


def _callback_owner_ok(callback: CallbackQuery, owner_id: int | None) -> bool:
    return owner_id is None or int(callback.from_user.id) == int(owner_id)


def _parse_frog_bet(raw: str | None) -> tuple[bool, int, str]:
    try:
        amount = int((raw or "").strip().split()[0])
    except (AttributeError, IndexError, ValueError):
        return False, 0, "❌ To'g'ri summa kiriting. Masalan: 1000"
    if amount < FROG_MIN_BET or amount > FROG_MAX_BET:
        return False, 0, f"❌ Stavka <b>{FROG_MIN_BET}</b> dan <b>{FROG_MAX_BET}</b> coin gacha bo'lishi kerak."
    return True, amount, ""


def _parse_mines_bet(raw: str | None) -> tuple[bool, int, str]:
    ok, amount, error = MinesAntiCheatValidator.validate_bet(raw)
    if not ok:
        return False, 0, error
    return True, amount, ""


def _parse_roulette_bet(raw: str | None) -> tuple[bool, int, str]:
    try:
        amount = int((raw or "").strip().split()[0])
    except (AttributeError, IndexError, ValueError):
        return False, 0, "❌ To'g'ri summa kiriting. Masalan: 1000"
    if amount < ROULETTE_MIN_BET or amount > ROULETTE_MAX_BET:
        return False, 0, f"❌ Stavka <b>{ROULETTE_MIN_BET}</b> dan <b>{ROULETTE_MAX_BET}</b> dollargacha bo'lishi kerak."
    return True, amount, ""


def _parse_chicken_bet(raw: str | None) -> tuple[bool, int, str]:
    try:
        amount = int((raw or "").strip().split()[0])
    except (AttributeError, IndexError, ValueError):
        return False, 0, "❌ To'g'ri summa kiriting. Masalan: 1000"
    if amount < CHICKEN_MIN_BET or amount > CHICKEN_MAX_BET:
        return False, 0, f"❌ Stavka <b>{CHICKEN_MIN_BET}</b> dan <b>{CHICKEN_MAX_BET}</b> coin gacha bo'lishi kerak."
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
    if await _check_daily_limit_message(message):
        return False
    return True


@router.message(Command("qimor", "frog"))
async def cmd_qimor(message: Message, command: CommandObject, engine: GameEngine, state: FSMContext) -> None:
    if not await _gamble_allowed_or_reply(message, engine):
        return
    await state.clear()
    command_name = (command.command or "").lower()
    if command_name == "qimor":
        await message.answer(_gamble_menu_text(), reply_markup=_gamble_menu_keyboard(message.from_user.id))
        return

    frog = FrogRoadEngine(SessionLocal)
    if command.args:
        ok, amount, error = _parse_frog_bet(command.args)
        if not ok:
            await message.answer(error, reply_markup=build_frog_start_keyboard(message.from_user.id))
            return
        view = await frog.start_frog_game(message.from_user, message.chat.id, amount)
        sent = await message.answer(view.text, reply_markup=view.keyboard)
        if view.session_id:
            await frog.set_message_id(view.session_id, sent.message_id)
        return
    await message.answer(frog_start_text(), reply_markup=build_frog_start_keyboard(message.from_user.id))


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


@router.message(MinesBetState.waiting_amount)
async def mines_custom_bet_amount(message: Message, state: FSMContext, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if not await _gamble_allowed_or_reply(message, engine):
        await state.clear()
        return
    ok, amount, error = _parse_mines_bet(message.text)
    if not ok:
        await message.answer(error or "❌ To'g'ri summa kiriting. Masalan: 1000")
        return
    await state.clear()
    mines = MinesEngine(SessionLocal)
    view = await mines.start_or_resume(message.from_user, message.chat.id, str(amount))
    sent = await message.answer(view.text, reply_markup=view.keyboard)
    if view.game_id and view.token:
        await mines.set_message_id(view.game_id, view.token, sent.message_id)


@router.message(RouletteBetState.waiting_amount)
async def roulette_custom_bet_amount(message: Message, state: FSMContext, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if not ROULETTE_ENABLED:
        await state.clear()
        await message.answer(_disabled_game_text("Ruletka"))
        return
    if not await _gamble_allowed_or_reply(message, engine):
        await state.clear()
        return
    ok, amount, error = _parse_roulette_bet(message.text)
    if not ok:
        await message.answer(error or "❌ To'g'ri summa kiriting. Masalan: 1000")
        return
    await state.clear()
    await message.answer(roulette_color_text(amount), reply_markup=roulette_color_keyboard(message.from_user.id, amount))


@router.message(ChickenBetState.waiting_amount)
async def chicken_custom_bet_amount(message: Message, state: FSMContext, engine: GameEngine) -> None:
    if message.from_user is None:
        return
    if not CHICKEN_ROAD_ENABLED:
        await state.clear()
        await message.answer(_disabled_game_text("Chicken Road"))
        return
    if not await _gamble_allowed_or_reply(message, engine):
        await state.clear()
        return
    ok, amount, error = _parse_chicken_bet(message.text)
    if not ok:
        await message.answer(error or "❌ To'g'ri summa kiriting. Masalan: 1000")
        return
    await state.clear()
    chicken = ChickenRoadEngine(SessionLocal)
    view = await chicken.start_chicken_game(message.from_user, message.chat.id, amount)
    sent = await message.answer(view.text, reply_markup=view.keyboard)
    if view.session_id:
        await chicken.set_message_id(view.session_id, sent.message_id)


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


@router.callback_query(F.data.startswith("qmenu:"))
async def gamble_menu_callback(callback: CallbackQuery, engine: GameEngine, state: FSMContext) -> None:
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
    if await _check_daily_limit_callback(callback):
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return
    action = parts[1]
    owner_id = int(parts[2])
    if not _callback_owner_ok(callback, owner_id):
        await callback.answer("❌ Bu menyu sizniki emas.", show_alert=True)
        return
    await state.clear()
    if action == "frog":
        await _edit_or_answer(callback.message, FrogView(frog_start_text(), build_frog_start_keyboard(owner_id)))
        await callback.answer("Qurbaqa Yo'li tanlandi.")
    elif action == "mines":
        await callback.message.edit_text(_mines_start_text(), reply_markup=_mines_bet_keyboard(owner_id))
        await callback.answer("Mines tanlandi.")
    elif action == "treasure":
        await callback.message.edit_text(treasure_start_text(), reply_markup=build_treasure_start_keyboard(owner_id))
        await callback.answer("Treasure Hunt tanlandi.")
    elif action == "roulette":
        if not ROULETTE_ENABLED:
            await callback.answer(_disabled_game_text("Ruletka"), show_alert=True)
            return
        await callback.message.edit_text(roulette_start_text(), reply_markup=roulette_bet_keyboard(owner_id))
        await callback.answer("Ruletka tanlandi.")
    elif action == "chicken":
        if not CHICKEN_ROAD_ENABLED:
            await callback.answer(_disabled_game_text("Chicken Road"), show_alert=True)
            return
        await callback.message.edit_text(chicken_start_text(), reply_markup=build_chicken_start_keyboard(owner_id))
        await callback.answer("Chicken Road tanlandi.")
    elif action == "back":
        await callback.message.edit_text(_gamble_menu_text(), reply_markup=_gamble_menu_keyboard(owner_id))
        await callback.answer("Menyu.")
    elif action == "cancel":
        await callback.message.edit_text("❌ Qimor menyusi bekor qilindi.")
        await callback.answer("Bekor qilindi.")
    else:
        await callback.answer("Callback noto'g'ri.", show_alert=True)


@router.callback_query(F.data.startswith("gmnew:"))
async def mines_start_callback(callback: CallbackQuery, engine: GameEngine, state: FSMContext) -> None:
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
    if await _check_daily_limit_callback(callback):
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 3 or not parts[2].isdigit():
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return
    action = parts[1]
    owner_id = int(parts[2])
    if not _callback_owner_ok(callback, owner_id):
        await callback.answer("❌ Bu o'yin menyusi sizniki emas.", show_alert=True)
        return
    if action == "custom":
        await state.set_state(MinesBetState.waiting_amount)
        await callback.message.answer("✍️ Mines stavkasini kiriting. Masalan: <code>1000</code>")
        await callback.answer("Summani yozing.")
        return
    if action != "start" or len(parts) != 4 or not parts[3].isdigit():
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return
    await state.clear()
    amount = int(parts[3])
    mines = MinesEngine(SessionLocal)
    view = await mines.start_or_resume(callback.from_user, callback.message.chat.id, str(amount))
    try:
        await callback.message.edit_text(view.text, reply_markup=view.keyboard)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await callback.message.answer(view.text, reply_markup=view.keyboard)
    if view.game_id and view.token:
        await mines.set_message_id(view.game_id, view.token, callback.message.message_id)
    await callback.answer(view.alert or "Mines yaratildi.", show_alert=view.show_alert)


@router.callback_query(F.data.startswith("thnew:"))
async def treasure_start_callback(callback: CallbackQuery, engine: GameEngine, state: FSMContext) -> None:
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
    if await _check_daily_limit_callback(callback):
        return
    try:
        action, owner_id, amount = parse_treasure_new_callback(callback.data or "")
    except ValueError:
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return
    if not _callback_owner_ok(callback, owner_id):
        await callback.answer("❌ Bu o'yin menyusi sizniki emas.", show_alert=True)
        return
    if action != "start" or amount is None:
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return
    await state.clear()
    treasure = TreasureHuntEngine(SessionLocal)
    view = await treasure.create_game(callback.from_user, callback.message.chat.id, int(amount))
    try:
        await callback.message.edit_text(view.text, reply_markup=view.keyboard)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await callback.message.answer(view.text, reply_markup=view.keyboard)
    if view.game_id and view.token:
        await treasure.set_message_id(view.game_id, view.token, callback.message.message_id)
    await callback.answer(view.alert or "Treasure Hunt yaratildi.", show_alert=view.show_alert)


@router.callback_query(F.data.startswith("th:"))
async def treasure_callback(callback: CallbackQuery, engine: GameEngine) -> None:
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
    try:
        action, game_id, token, cell = parse_treasure_callback(callback.data or "")
    except ValueError:
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return
    if action == "noop":
        await callback.answer()
        return
    if action in {"j", "join"} and await _check_daily_limit_callback(callback):
        return

    treasure = TreasureHuntEngine(SessionLocal)
    if action in {"j", "join"}:
        view = await treasure.join(callback.from_user, game_id, token)
    elif action == "start":
        view = await treasure.start(callback.from_user.id, game_id, token)
    elif action in {"p", "pick"}:
        if cell is None:
            await callback.answer("Katak noto'g'ri.", show_alert=True)
            return
        view = await treasure.pick(callback.from_user.id, game_id, token, cell)
    elif action == "cancel":
        view = await treasure.cancel(callback.from_user.id, game_id, token)
    else:
        await callback.answer("Bu tugma eskirgan. /qimor bilan qayta oching.", show_alert=True)
        return

    if view.text:
        try:
            await callback.message.edit_text(view.text, reply_markup=view.keyboard)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.message.answer(view.text, reply_markup=view.keyboard)
    if view.game_id and view.token:
        await treasure.set_message_id(view.game_id, view.token, callback.message.message_id)
    await callback.answer(view.alert or "OK", show_alert=view.show_alert)


@router.callback_query(F.data.startswith("roulette:"))
async def roulette_callback(callback: CallbackQuery, engine: GameEngine, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not ROULETTE_ENABLED:
        await state.clear()
        await callback.answer(_disabled_game_text("Ruletka"), show_alert=True)
        return
    if not await engine.is_gamble_enabled():
        await callback.answer("Bu xizmat vaqtinchalik ishlamaydi. Admin tomonidan cheklangan.", show_alert=True)
        return
    allowed, _link = await engine.gamble_chat_check(callback.message.chat.id)
    if not allowed:
        await callback.answer("🎰 Qimor bu guruhda ochilmagan. /qimor yozib to'lov qiling.", show_alert=True)
        return
    if await _check_daily_limit_callback(callback):
        return
    try:
        action, owner_id, amount, choice = parse_roulette_callback(callback.data or "")
    except ValueError:
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return
    if action == "noop":
        await callback.answer("Yangi stavka uchun /qimor yozing.")
        return
    if owner_id is None or not _callback_owner_ok(callback, owner_id):
        await callback.answer("❌ Bu ruletka menyusi sizniki emas.", show_alert=True)
        return
    if action == "menu":
        await state.clear()
        await callback.message.edit_text(roulette_start_text(), reply_markup=roulette_bet_keyboard(owner_id))
        await callback.answer("Ruletka.")
        return
    if action == "custom":
        await state.set_state(RouletteBetState.waiting_amount)
        await callback.message.answer(
            f"✍️ Ruletka stavkasini kiriting.\nMinimal: <b>{ROULETTE_MIN_BET}</b> dollar\nMaksimal: <b>{ROULETTE_MAX_BET}</b> dollar"
        )
        await callback.answer("Summani yozing.")
        return
    if action == "bet":
        if amount is None:
            await callback.answer("Stavka noto'g'ri.", show_alert=True)
            return
        await state.clear()
        await callback.message.edit_text(roulette_color_text(amount), reply_markup=roulette_color_keyboard(owner_id, amount))
        await callback.answer("Rangni tanlang.")
        return
    if action == "place":
        if amount is None or choice is None:
            await callback.answer("Stavka noto'g'ri.", show_alert=True)
            return
        await state.clear()
        roulette = RouletteEngine(SessionLocal)
        view = await roulette.place_bet(callback.from_user, callback.message.chat.id, amount, choice)
        try:
            await callback.message.edit_text(view.text, reply_markup=view.keyboard)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.message.answer(view.text, reply_markup=view.keyboard)
        if view.round_id:
            await roulette.set_message_id(view.round_id, callback.message.message_id)
        await callback.answer(view.alert or "Stavka qabul qilindi.", show_alert=view.show_alert)
        return
    await callback.answer("Callback noto'g'ri.", show_alert=True)


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
    if await _check_daily_limit_callback(callback):
        return
    try:
        action, value, column, owner_id = parse_frog_callback(callback.data or "")
    except ValueError:
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return
    if not _callback_owner_ok(callback, owner_id):
        await callback.answer("❌ Bu o'yin menyusi sizniki emas.", show_alert=True)
        return

    frog = FrogRoadEngine(SessionLocal)
    if action == "menu_cancel":
        await state.clear()
        try:
            await callback.message.edit_text("❌ Qurbaqa Yo'li menyusi bekor qilindi.")
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.message.answer("❌ Qurbaqa Yo'li menyusi bekor qilindi.")
        await callback.answer("Bekor qilindi.")
        return
    if action == "custom_bet":
        await state.set_state(FrogBetState.waiting_amount)
        await callback.message.answer(
            f"✍️ Stavka summasini kiriting.\nMinimal: <b>{FROG_MIN_BET}</b> coin\nMaksimal: <b>{FROG_MAX_BET}</b> coin"
        )
        await callback.answer("Summani yozing.")
        return
    if action == "noop":
        await callback.answer()
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
    if action in {"jump", "cashout"}:
        await _enforce_overwin_for_user(callback)
    await callback.answer(view.alert or "OK", show_alert=view.show_alert)


@router.callback_query(F.data.startswith("chicken:") | (F.data == "qimor:chicken:start"))
async def chicken_callback(callback: CallbackQuery, engine: GameEngine, state: FSMContext) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer("Callback eskirgan.", show_alert=True)
        return
    if not CHICKEN_ROAD_ENABLED:
        await state.clear()
        await callback.answer(_disabled_game_text("Chicken Road"), show_alert=True)
        return
    if not await engine.is_gamble_enabled():
        await callback.answer("Bu xizmat vaqtinchalik ishlamaydi. Admin tomonidan cheklangan.", show_alert=True)
        return
    allowed, _link = await engine.gamble_chat_check(callback.message.chat.id)
    if not allowed:
        await callback.answer("🎰 Qimor bu guruhda ochilmagan. /qimor yozib to'lov qiling.", show_alert=True)
        return
    if await _check_daily_limit_callback(callback):
        return
    try:
        action, value, owner_id = parse_chicken_callback(callback.data or "")
    except ValueError:
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return
    if not _callback_owner_ok(callback, owner_id):
        await callback.answer("❌ Bu o'yin menyusi sizniki emas.", show_alert=True)
        return

    chicken = ChickenRoadEngine(SessionLocal)
    if action == "menu":
        await state.clear()
        await callback.message.edit_text(chicken_start_text(), reply_markup=build_chicken_start_keyboard(callback.from_user.id))
        await callback.answer("Chicken Road.")
        return
    if action == "back":
        await state.clear()
        await callback.message.edit_text(
            _gamble_menu_text(),
            reply_markup=_gamble_menu_keyboard(callback.from_user.id),
        )
        await callback.answer("Menyu.")
        return
    if action == "custom_bet":
        await state.set_state(ChickenBetState.waiting_amount)
        await callback.message.answer(
            f"✍️ Chicken Road stavkasini kiriting.\nMinimal: <b>{CHICKEN_MIN_BET}</b> coin\nMaksimal: <b>{CHICKEN_MAX_BET}</b> coin"
        )
        await callback.answer("Summani yozing.")
        return
    if action == "noop":
        await callback.answer()
        return
    if action == "bet":
        if value is None:
            await callback.answer("Stavka noto'g'ri.", show_alert=True)
            return
        await state.clear()
        view = await chicken.start_chicken_game(callback.from_user, callback.message.chat.id, int(value))
        try:
            await callback.message.edit_text(view.text, reply_markup=view.keyboard)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.message.answer(view.text, reply_markup=view.keyboard)
        if view.session_id:
            await chicken.set_message_id(view.session_id, callback.message.message_id)
        await callback.answer(view.alert or "O'yin boshlandi.", show_alert=view.show_alert)
        return
    if action == "go":
        if value is None:
            await callback.answer("Callback noto'g'ri.", show_alert=True)
            return
        view = await chicken.handle_chicken_go(callback.from_user.id, value)
    elif action == "cashout":
        if value is None:
            await callback.answer("Callback noto'g'ri.", show_alert=True)
            return
        view = await chicken.handle_chicken_cashout(callback.from_user.id, value)
    elif action == "cancel":
        if value is None:
            await callback.answer("Callback noto'g'ri.", show_alert=True)
            return
        view = await chicken.handle_chicken_cancel(callback.from_user.id, value)
    else:
        await callback.answer("Callback noto'g'ri.", show_alert=True)
        return

    if view.text:
        try:
            await callback.message.edit_text(view.text, reply_markup=view.keyboard)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.message.answer(view.text, reply_markup=view.keyboard)
    if action in {"go", "cashout"}:
        await _enforce_overwin_for_user(callback)
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
    if await _check_daily_limit_callback(callback):
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
            if action in {"o", "p", "open", "c", "cashout"}:
                await _enforce_overwin_for_mines_game(callback, game_id)
            await callback.answer(view.alert or "OK", show_alert=view.show_alert)
            return

    if view.text:
        try:
            await callback.message.edit_text(view.text, reply_markup=view.keyboard)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await callback.message.answer(view.text, reply_markup=view.keyboard)
    if action in {"o", "p", "open", "c", "cashout"}:
        await _enforce_overwin_for_mines_game(callback, game_id)
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
