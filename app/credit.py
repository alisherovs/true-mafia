from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CreditBlockedUser, CreditLoan, DollarTransaction, User


CREDIT_AMOUNTS = {1000, 2500, 5000, 7500, 10000}
CREDIT_DAYS = set(range(1, 8))
CREDIT_INTEREST_RATE = 0.10
MAX_ACTIVE_CREDIT = 10_000
TASHKENT_TZ = timezone(timedelta(hours=5))
_CREDIT_LOCKS: dict[int, asyncio.Lock] = {}


class CreditService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    @staticmethod
    def calculate(amount: int, days: int) -> tuple[int, int, int]:
        interest = ceil(amount * CREDIT_INTEREST_RATE)
        total = amount + interest
        daily = ceil(total / max(1, days))
        return interest, total, daily

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def _due_at_for_days(cls, days: int) -> datetime:
        local_now = cls._now().astimezone(TASHKENT_TZ)
        local_due = (local_now + timedelta(days=days)).replace(hour=10, minute=0, second=0, microsecond=0)
        return local_due.astimezone(timezone.utc)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def _record_dollar(session: AsyncSession, user: User, amount: int, action: str, note: str = "") -> None:
        if amount == 0:
            return
        session.add(
            DollarTransaction(
                user_telegram_id=user.telegram_id,
                user_name=(user.display_name or "User")[:255],
                amount=int(amount),
                balance_after=int(user.dollar or 0),
                action=action[:64],
                note=note or None,
            )
        )

    async def is_blocked(self, telegram_id: int) -> bool:
        async with self.session_factory() as session:
            row = await session.get(CreditBlockedUser, telegram_id)
            return row is not None

    async def active_loan(self, telegram_id: int) -> Optional[CreditLoan]:
        async with self.session_factory() as session:
            return (
                await session.execute(
                    select(CreditLoan)
                    .where(CreditLoan.user_telegram_id == telegram_id, CreditLoan.status == "active")
                    .order_by(CreditLoan.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def menu_text(self, telegram_id: int) -> tuple[str, bool]:
        async with self.session_factory() as session:
            blocked = await session.get(CreditBlockedUser, telegram_id)
            if blocked is not None:
                return (
                    "🚫 <b>Kredit bloki</b>\n\n"
                    "Siz kredit muddatini o'tkazib yuborganingiz uchun bot kredit tizimidan bloklangansiz.",
                    False,
                )
            loan = (
                await session.execute(
                    select(CreditLoan)
                    .where(CreditLoan.user_telegram_id == telegram_id, CreditLoan.status == "active")
                    .order_by(CreditLoan.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if loan is None:
                return (
                    "💳 <b>Kredit bo'limi</b>\n\n"
                    "Faqat dollar krediti beriladi.\n"
                    f"Maksimal kredit: <b>{MAX_ACTIVE_CREDIT}</b> dollar\n"
                    "Soliq: <b>10%</b>\n"
                    "Muddat: <b>1-7 kun</b>\n\n"
                    "Kerakli kredit miqdorini tanlang.",
                    False,
                )
            return self._loan_text(loan), True

    def amount_text(self, amount: int) -> str:
        if amount not in CREDIT_AMOUNTS:
            return "Noto'g'ri kredit miqdori."
        interest, total, _ = self.calculate(amount, 1)
        return (
            "💳 <b>Kredit muddati</b>\n\n"
            f"Beriladigan summa: <b>{amount}</b> dollar\n"
            f"10% soliq: <b>{interest}</b> dollar\n"
            f"Jami qaytariladi: <b>{total}</b> dollar\n\n"
            "Necha kunga olmoqchisiz?"
        )

    def confirm_text(self, amount: int, days: int) -> str:
        interest, total, daily = self.calculate(amount, days)
        return (
            "💳 <b>Kreditni tasdiqlash</b>\n\n"
            f"Beriladi: <b>{amount}</b> dollar\n"
            f"Muddat: <b>{days}</b> kun\n"
            f"Soliq: <b>{interest}</b> dollar\n"
            f"Jami qaytarish: <b>{total}</b> dollar\n"
            f"Kunlik hisob: <b>{daily}</b> dollar\n\n"
            "Muddat tugaganda balansingizda mablag' yetarli bo'lsa avtomatik yechiladi. "
            "Yetarli bo'lmasa kredit blok ro'yxatiga kiritilasiz."
        )

    async def take_credit(self, tg_user, amount: int, days: int) -> tuple[bool, str]:
        if amount not in CREDIT_AMOUNTS or amount > MAX_ACTIVE_CREDIT:
            return False, "Noto'g'ri kredit miqdori."
        if days not in CREDIT_DAYS:
            return False, "Kredit muddati 1 kundan 7 kungacha bo'lishi kerak."
        lock = _credit_lock(tg_user.id)
        await lock.acquire()
        try:
            return await self._take_credit_locked(tg_user, amount, days)
        finally:
            lock.release()

    async def _take_credit_locked(self, tg_user, amount: int, days: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            if await session.get(CreditBlockedUser, tg_user.id) is not None:
                return False, "🚫 Siz kredit tizimidan bloklangansiz."
            user = (
                await session.execute(select(User).where(User.telegram_id == tg_user.id))
            ).scalar_one_or_none()
            if user is None:
                user = User(
                    telegram_id=tg_user.id,
                    username=getattr(tg_user, "username", None),
                    display_name=(getattr(tg_user, "full_name", None) or "User")[:255],
                    language="uz",
                )
                session.add(user)
                await session.flush()
            active = (
                await session.execute(
                    select(CreditLoan)
                    .where(CreditLoan.user_telegram_id == tg_user.id, CreditLoan.status == "active")
                    .limit(1)
                )
            ).scalar_one_or_none()
            if active is not None:
                return False, "Sizda aktiv kredit bor. Avval uni so'ndiring."
            interest, total, _ = self.calculate(amount, days)
            user.dollar = int(user.dollar or 0) + amount
            loan = CreditLoan(
                user_telegram_id=tg_user.id,
                principal=amount,
                interest=interest,
                total_due=total,
                term_days=days,
                status="active",
                due_at=self._due_at_for_days(days),
            )
            session.add(loan)
            self._record_dollar(session, user, amount, "credit_loan", note=f"Kredit olindi: {days} kun, jami qaytarish {total}")
            await session.commit()
        return True, (
            "✅ <b>Kredit berildi</b>\n\n"
            f"Hisobingizga <b>{amount}</b> dollar qo'shildi.\n"
            f"Qaytariladigan summa: <b>{total}</b> dollar\n"
            f"Muddat: <b>{days}</b> kun"
        )

    async def repay(self, telegram_id: int) -> tuple[bool, str]:
        lock = _credit_lock(telegram_id)
        await lock.acquire()
        try:
            return await self._repay_locked(telegram_id)
        finally:
            lock.release()

    async def _repay_locked(self, telegram_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            loan = (
                await session.execute(
                    select(CreditLoan)
                    .where(CreditLoan.user_telegram_id == telegram_id, CreditLoan.status == "active")
                    .with_for_update()
                    .limit(1)
                )
            ).scalar_one_or_none()
            if loan is None:
                return False, "Aktiv kredit topilmadi."
            user = (
                await session.execute(select(User).where(User.telegram_id == telegram_id).with_for_update())
            ).scalar_one_or_none()
            if user is None:
                return False, "Profil topilmadi."
            if int(user.dollar or 0) < int(loan.total_due):
                return False, f"Balans yetarli emas. Kerak: <b>{int(loan.total_due)}</b> dollar."
            user.dollar = int(user.dollar or 0) - int(loan.total_due)
            loan.status = "paid"
            loan.paid_at = self._now()
            self._record_dollar(session, user, -int(loan.total_due), "credit_repay", note=f"Kredit #{loan.id} so'ndirildi")
            await session.commit()
        return True, f"✅ Kredit so'ndirildi. Balansingizdan <b>{int(loan.total_due)}</b> dollar yechildi."

    async def daily_watchdog(self, bot: Bot) -> None:
        now = self._now()
        async with self.session_factory() as session:
            loans = (
                await session.execute(
                    select(CreditLoan)
                    .where(CreditLoan.status == "active")
                    .order_by(CreditLoan.due_at.asc())
                    .limit(200)
                )
            ).scalars().all()
            for loan in loans:
                user = (
                    await session.execute(select(User).where(User.telegram_id == loan.user_telegram_id).with_for_update())
                ).scalar_one_or_none()
                if user is None:
                    continue
                due_at = self._aware(loan.due_at)
                days_left = max(0, ceil((due_at - now).total_seconds() / 86400))
                if due_at <= now:
                    if int(user.dollar or 0) >= int(loan.total_due):
                        user.dollar = int(user.dollar or 0) - int(loan.total_due)
                        loan.status = "paid"
                        loan.paid_at = now
                        self._record_dollar(session, user, -int(loan.total_due), "credit_auto_repay", note=f"Kredit #{loan.id} avtomatik so'ndirildi")
                        await self._safe_send(
                            bot,
                            user.telegram_id,
                            f"✅ Kreditingiz avtomatik so'ndirildi.\nYechildi: <b>{int(loan.total_due)}</b> dollar.",
                        )
                    else:
                        loan.status = "defaulted"
                        blocked = await session.get(CreditBlockedUser, user.telegram_id)
                        if blocked is None:
                            session.add(
                                CreditBlockedUser(
                                    telegram_id=user.telegram_id,
                                    display_name=(user.display_name or "User")[:255],
                                    reason=f"Kredit #{loan.id} muddatida so'ndirilmadi",
                                    loan_id=loan.id,
                                )
                            )
                        await self._safe_send(
                            bot,
                            user.telegram_id,
                            "🚫 Kredit muddatida so'ndirilmadi. Balansingiz yetarli bo'lmagani uchun bot kredit blok ro'yxatiga kiritildingiz.",
                        )
                    continue

                loan.last_reminder_at = now
                await self._safe_send(
                    bot,
                    user.telegram_id,
                    "⚠️ <b>Kredit eslatmasi</b>\n\n"
                    f"Qaytariladigan summa: <b>{int(loan.total_due)}</b> dollar\n"
                    f"Qolgan muddat: <b>{days_left}</b> kun\n\n"
                    "Kredit so'ndirilmasa botdan bloklanasiz.",
                )
            await session.commit()

    async def _safe_send(self, bot: Bot, telegram_id: int, text: str) -> None:
        try:
            await bot.send_message(telegram_id, text)
        except (TelegramForbiddenError, TelegramBadRequest):
            return

    def _loan_text(self, loan: CreditLoan) -> str:
        due_at = self._aware(loan.due_at)
        now = self._now()
        seconds_left = max(0, int((due_at - now).total_seconds()))
        days_left = max(0, ceil(seconds_left / 86400))
        return (
            "💳 <b>Aktiv kredit</b>\n\n"
            f"Olingan summa: <b>{int(loan.principal)}</b> dollar\n"
            f"10% soliq: <b>{int(loan.interest)}</b> dollar\n"
            f"So'ndirish summasi: <b>{int(loan.total_due)}</b> dollar\n"
            f"Muddat: <b>{int(loan.term_days)}</b> kun\n"
            f"Qolgan vaqt: <b>{days_left}</b> kun\n\n"
            "Kreditni hoziroq so'ndirish uchun pastdagi tugmani bosing."
        )


def _credit_lock(telegram_id: int) -> asyncio.Lock:
    lock = _CREDIT_LOCKS.get(telegram_id)
    if lock is None:
        lock = asyncio.Lock()
        _CREDIT_LOCKS[telegram_id] = lock
    return lock
