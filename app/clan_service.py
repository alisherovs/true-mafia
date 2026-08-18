from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from math import ceil
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    Clan,
    ClanApplication,
    ClanAuditLog,
    ClanGameReward,
    ClanMember,
    ClanTransaction,
    DiamondTransaction,
    DollarTransaction,
    User,
)

logger = logging.getLogger(__name__)

CLAN_CREATE_DIAMOND_COST = 30
CLAN_RENAME_DIAMOND_COST = 5
CLAN_BIO_DOLLAR_COST = 10_000

WIN_XP = 10
LOSS_XP = 2

# Custom Telegram Emoji IDs provided by user
CUSTOM_EMOJIS = {
    "clan": "5242499702120809395",
    "owner": "5156877291397055163",
    "admin": "5251203410396458957",
    "member": "5453957997418004470",
    "members": "5427168083074628963",
    "diamond": "5427168083074628963",
    "dollar": "5409048419211682843",
    "level": "5438496463044752972",
    "xp": "5834775782433492415",
    "wins": "5226431245918942763",
    "losses": "5264738389944450167",
    "games": "5361741454685256344",
    "stats": "5071491301443110142",
    "power": "5424972470023104089",
    "date": "5413879192267805083",
    "bio": "5395444784611480792",
    "edit": "5253742260054409879",
    "apps": "5386367538735104399",
    "pending": "5296369303661067030",
    "locked": "5397916757333654639",
    "create": "5017088445353296841",
    "search": "5381868856845302896",
    "leave": "5019500511871632068",
    "delete": "5440539497383087970",
    "activity": "5447203607294265305",
}


def format_emoji(key: str, fallback: str) -> str:
    emoji_id = CUSTOM_EMOJIS.get(key)
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback


def calc_member_limit(level: int) -> int:
    return 10 + max(0, level - 1) * 5


def xp_for_level(level: int) -> int:
    return level * 500


def calc_clan_power(wins: int, total_games: int, xp: int, level: int, member_count: int) -> int:
    win_rate = (wins / total_games * 100) if total_games > 0 else 0
    power = int((wins * 20) + (win_rate * 50) + (xp * 0.5) + (level * 200) + (member_count * 50))
    return max(0, power)


class ClanService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    async def get_user_clan_info(self, telegram_id: int) -> tuple[str, Optional[Clan], Optional[ClanMember], int]:
        """
        Returns: (status, clan, member_record, pending_apps_count)
        status: 'clanless' | 'member' | 'owner'
        """
        async with self.session_factory() as session:
            member = (
                await session.execute(
                    select(ClanMember)
                    .join(Clan, ClanMember.clan_id == Clan.id)
                    .where(ClanMember.user_telegram_id == telegram_id, Clan.status == "active")
                )
            ).scalar_one_or_none()

            if member is None:
                return "clanless", None, None, 0

            clan = await session.get(Clan, member.clan_id)
            if clan is None or clan.status != "active":
                return "clanless", None, None, 0

            pending_count = 0
            if clan.owner_id == telegram_id or member.role == "owner":
                status = "owner"
                pending_count = int(
                    await session.scalar(
                        select(func.count(ClanApplication.id)).where(
                            ClanApplication.clan_id == clan.id,
                            ClanApplication.status == "pending",
                        )
                    )
                    or 0
                )
            else:
                status = "member"

            return status, clan, member, pending_count

    async def create_clan(self, tg_user, name: str, bio: str = "") -> tuple[bool, str, Optional[Clan]]:
        clean_name = name.strip()
        if len(clean_name) < 3 or len(clean_name) > 32:
            return False, "❌ Clan nomi 3 ta dan 32 ta belgigacha bo'lishi kerak.", None

        clean_bio = (bio or "We fight together!").strip()[:300]

        async with self.session_factory() as session:
            async with session.begin():
                # Check user exists & balance
                user = (
                    await session.execute(
                        select(User).where(User.telegram_id == tg_user.id).with_for_update()
                    )
                ).scalar_one_or_none()
                if user is None:
                    return False, "❌ Profil topilmadi.", None

                if int(user.diamonds or 0) < CLAN_CREATE_DIAMOND_COST:
                    return (
                        False,
                        f"❌ Balansingizda yetarli olmos yo'q. Kerak: <b>{CLAN_CREATE_DIAMOND_COST}</b> 💎.",
                        None,
                    )

                # Check if already in active clan
                existing_member = (
                    await session.execute(
                        select(ClanMember)
                        .join(Clan, ClanMember.clan_id == Clan.id)
                        .where(ClanMember.user_telegram_id == tg_user.id, Clan.status == "active")
                    )
                ).scalar_one_or_none()
                if existing_member is not None:
                    return False, "❌ Siz allaqachon biror Clan a'zosisiz.", None

                # Check unique name
                existing_clan = (
                    await session.execute(
                        select(Clan).where(func.lower(Clan.name) == clean_name.lower(), Clan.status == "active")
                    )
                ).scalar_one_or_none()
                if existing_clan is not None:
                    return False, f"❌ <b>{clean_name}</b> nomli Clan allaqachon mavjud.", None

                # Deduct diamonds
                user.diamonds = int(user.diamonds or 0) - CLAN_CREATE_DIAMOND_COST
                session.add(
                    DiamondTransaction(
                        user_telegram_id=user.telegram_id,
                        user_name=(user.display_name or "User")[:255],
                        amount=-CLAN_CREATE_DIAMOND_COST,
                        balance_after=user.diamonds,
                        action="create_clan",
                        note=f"Yangi clan yaratildi: {clean_name}",
                    )
                )

                # Create clan
                clan = Clan(
                    name=clean_name,
                    bio=clean_bio,
                    owner_id=tg_user.id,
                    creator_id=tg_user.id,
                    level=1,
                    xp=0,
                    wins=0,
                    losses=0,
                    total_games=0,
                    rating=0,
                    member_limit=BASE_MEMBER_LIMIT,
                    status="active",
                )
                session.add(clan)
                await session.flush()

                # Add owner as first member
                member = ClanMember(
                    clan_id=clan.id,
                    user_id=user.id,
                    user_telegram_id=tg_user.id,
                    role="owner",
                    joined_at=self._now(),
                )
                session.add(member)

                # Record transaction & audit log
                session.add(
                    ClanTransaction(
                        clan_id=clan.id,
                        user_telegram_id=tg_user.id,
                        type="create_clan",
                        currency="diamonds",
                        amount=CLAN_CREATE_DIAMOND_COST,
                        description=f"Clan yaratildi: {clean_name}",
                    )
                )
                session.add(
                    ClanAuditLog(
                        clan_id=clan.id,
                        actor_telegram_id=tg_user.id,
                        action="create_clan",
                        metadata_json=json.dumps({"name": clean_name, "cost": CLAN_CREATE_DIAMOND_COST}),
                    )
                )

        return True, f"🎉 <b>{clean_name}</b> Clani muvaffaqiyatli yaratildi!", clan

    async def apply_to_clan(self, tg_user, clan_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            async with session.begin():
                user = (
                    await session.execute(select(User).where(User.telegram_id == tg_user.id))
                ).scalar_one_or_none()
                if user is None:
                    return False, "❌ Avval botdan /start o'ting."

                # Check if already in active clan
                in_clan = (
                    await session.execute(
                        select(ClanMember)
                        .join(Clan, ClanMember.clan_id == Clan.id)
                        .where(ClanMember.user_telegram_id == tg_user.id, Clan.status == "active")
                    )
                ).scalar_one_or_none()
                if in_clan is not None:
                    return False, "❌ Siz allaqachon boshqa Clan a'zosisiz."

                clan = await session.get(Clan, clan_id)
                if clan is None or clan.status != "active":
                    return False, "❌ Ushbu Clan topilmadi yoki o'chirilgan."

                # Check member count limit
                current_members = int(
                    await session.scalar(
                        select(func.count(ClanMember.id)).where(ClanMember.clan_id == clan.id)
                    )
                    or 0
                )
                limit = clan.member_limit or calc_member_limit(clan.level)
                if current_members >= limit:
                    return False, "🔒 Ushbu Clan a'zolari bilan to'lgan."

                # Check pending application for this clan
                existing_app = (
                    await session.execute(
                        select(ClanApplication).where(
                            ClanApplication.clan_id == clan.id,
                            ClanApplication.user_telegram_id == tg_user.id,
                            ClanApplication.status == "pending",
                        )
                    )
                ).scalar_one_or_none()
                if existing_app is not None:
                    return False, "⏳ Siz allaqachon ushbu Clan'ga ariza yuborgansiz. Ko'rib chiqilishi kutilmoqda."

                # Create application
                app_rec = ClanApplication(
                    clan_id=clan.id,
                    user_id=user.id,
                    user_telegram_id=tg_user.id,
                    status="pending",
                )
                session.add(app_rec)
                session.add(
                    ClanAuditLog(
                        clan_id=clan.id,
                        actor_telegram_id=tg_user.id,
                        action="apply_to_clan",
                    )
                )

        return True, "✅ Qo'shilish uchun ariza yuborildi! Owner ko'rib chiqqach xabar beriladi."

    async def process_application(
        self, bot: Bot, owner_telegram_id: int, app_id: int, action: str
    ) -> tuple[bool, str]:
        """action: 'accept' | 'reject'"""
        if action not in {"accept", "reject"}:
            return False, "Noto'g'ri amal."

        async with self.session_factory() as session:
            async with session.begin():
                app_rec = await session.get(ClanApplication, app_id)
                if app_rec is None or app_rec.status != "pending":
                    return False, "❌ Ushbu ariza topilmadi yoki allaqachon ko'rib chiqilgan."

                clan = await session.get(Clan, app_rec.clan_id)
                if clan is None or clan.status != "active":
                    return False, "❌ Clan faol emas."

                if clan.owner_id != owner_telegram_id:
                    return False, "🚫 Siz ushbu Clan owner'i emassiz."

                applicant = (
                    await session.execute(select(User).where(User.telegram_id == app_rec.user_telegram_id))
                ).scalar_one_or_none()

                applicant_name = applicant.display_name if applicant else "Foydalanuvchi"

                if action == "accept":
                    # Check member limit again
                    current_members = int(
                        await session.scalar(
                            select(func.count(ClanMember.id)).where(ClanMember.clan_id == clan.id)
                        )
                        or 0
                    )
                    limit = clan.member_limit or calc_member_limit(clan.level)
                    if current_members >= limit:
                        return False, "🔒 Clan sig'imi to'lgan. Ko'proq a'zo qo me olish uchun Level oshiring."

                    # Check applicant not joined another clan meanwhile
                    already_in = (
                        await session.execute(
                            select(ClanMember)
                            .join(Clan, ClanMember.clan_id == Clan.id)
                            .where(ClanMember.user_telegram_id == app_rec.user_telegram_id, Clan.status == "active")
                        )
                    ).scalar_one_or_none()
                    if already_in is not None:
                        app_rec.status = "cancelled"
                        app_rec.processed_at = self._now()
                        app_rec.processed_by = owner_telegram_id
                        return False, "❌ Ushbu foydalanuvchi allaqachon boshqa Clan'ga a'zo bo'lgan."

                    # Add member
                    member = ClanMember(
                        clan_id=clan.id,
                        user_id=app_rec.user_id,
                        user_telegram_id=app_rec.user_telegram_id,
                        role="member",
                        joined_at=self._now(),
                    )
                    session.add(member)

                    app_rec.status = "accepted"
                    app_rec.processed_at = self._now()
                    app_rec.processed_by = owner_telegram_id

                    session.add(
                        ClanAuditLog(
                            clan_id=clan.id,
                            actor_telegram_id=owner_telegram_id,
                            action="accept_application",
                            target_telegram_id=app_rec.user_telegram_id,
                        )
                    )

                    msg_to_applicant = f"🎉 <b>Tabriklaymiz!</b>\nSiz <b>{clan.name}</b> Clan'iga qabul qilindingiz!"
                    res_text = f"✅ {applicant_name} Clan'ga qabul qilindi!"
                else:
                    app_rec.status = "rejected"
                    app_rec.processed_at = self._now()
                    app_rec.processed_by = owner_telegram_id

                    session.add(
                        ClanAuditLog(
                            clan_id=clan.id,
                            actor_telegram_id=owner_telegram_id,
                            action="reject_application",
                            target_telegram_id=app_rec.user_telegram_id,
                        )
                    )

                    msg_to_applicant = f"ℹ️ Sizning <b>{clan.name}</b> Clan'iga yuborgan arizangiz rad etildi."
                    res_text = f"❌ Ariza rad etildi."

        # Notify applicant asynchronously outside transaction
        try:
            await bot.send_message(app_rec.user_telegram_id, msg_to_applicant)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

        return True, res_text

    async def leave_clan(self, telegram_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            async with session.begin():
                member = (
                    await session.execute(
                        select(ClanMember)
                        .join(Clan, ClanMember.clan_id == Clan.id)
                        .where(ClanMember.user_telegram_id == telegram_id, Clan.status == "active")
                    )
                ).scalar_one_or_none()
                if member is None:
                    return False, "❌ Siz hech qaysi Clan a'zosi emassiz."

                clan = await session.get(Clan, member.clan_id)
                if clan is None or clan.status != "active":
                    return False, "❌ Clan faol emas."

                if clan.owner_id == telegram_id or member.role == "owner":
                    return (
                        False,
                        "🚫 Siz Clan Owner'isiz. Avval Clan egaligini boshqa a'zoga topshiring yoki Clan'ni o'chiring.",
                    )

                await session.delete(member)
                session.add(
                    ClanAuditLog(
                        clan_id=clan.id,
                        actor_telegram_id=telegram_id,
                        action="leave_clan",
                    )
                )

        return True, f"🚪 Siz <b>{clan.name}</b> Clan'idan chiqdingiz."

    async def transfer_owner(self, owner_telegram_id: int, new_owner_telegram_id: int) -> tuple[bool, str]:
        if owner_telegram_id == new_owner_telegram_id:
            return False, "❌ O'zingizga qayta topshira olmaysiz."

        async with self.session_factory() as session:
            async with session.begin():
                owner_member = (
                    await session.execute(
                        select(ClanMember)
                        .join(Clan, ClanMember.clan_id == Clan.id)
                        .where(ClanMember.user_telegram_id == owner_telegram_id, Clan.status == "active")
                    )
                ).scalar_one_or_none()

                if owner_member is None:
                    return False, "❌ Clan topilmadi."

                clan = await session.get(Clan, owner_member.clan_id)
                if clan is None or clan.status != "active" or clan.owner_id != owner_telegram_id:
                    return False, "🚫 Siz ushbu Clan owner'i emassiz."

                target_member = (
                    await session.execute(
                        select(ClanMember).where(
                            ClanMember.clan_id == clan.id,
                            ClanMember.user_telegram_id == new_owner_telegram_id,
                        )
                    )
                ).scalar_one_or_none()
                if target_member is None:
                    return False, "❌ Tanlangan foydalanuvchi ushbu Clan a'zosi emas."

                # Transfer roles
                owner_member.role = "member"
                target_member.role = "owner"
                clan.owner_id = new_owner_telegram_id

                session.add(
                    ClanAuditLog(
                        clan_id=clan.id,
                        actor_telegram_id=owner_telegram_id,
                        action="transfer_owner",
                        target_telegram_id=new_owner_telegram_id,
                    )
                )

        return True, f"👑 Clan egaligi muvaffaqiyatli yangi foydalanuvchiga o'tkazildi!"

    async def change_clan_name(self, owner_telegram_id: int, new_name: str) -> tuple[bool, str]:
        clean_name = new_name.strip()
        if len(clean_name) < 3 or len(clean_name) > 32:
            return False, "❌ Nom 3 ta dan 32 ta belgigacha bo'lishi kerak."

        async with self.session_factory() as session:
            async with session.begin():
                clan = (
                    await session.execute(
                        select(Clan).where(Clan.owner_id == owner_telegram_id, Clan.status == "active")
                    )
                ).scalar_one_or_none()
                if clan is None:
                    return False, "🚫 Siz aktiv Clan owner'i emassiz."

                # Check unique name
                existing = (
                    await session.execute(
                        select(Clan).where(
                            func.lower(Clan.name) == clean_name.lower(),
                            Clan.id != clan.id,
                            Clan.status == "active",
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return False, f"❌ <b>{clean_name}</b> nomli Clan allaqachon bor."

                user = (
                    await session.execute(
                        select(User).where(User.telegram_id == owner_telegram_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if user is None or int(user.diamonds or 0) < CLAN_RENAME_DIAMOND_COST:
                    return False, f"❌ Balansingizda yetarli olmos yo'q. Kerak: <b>{CLAN_RENAME_DIAMOND_COST}</b> 💎."

                # Deduct
                user.diamonds = int(user.diamonds or 0) - CLAN_RENAME_DIAMOND_COST
                session.add(
                    DiamondTransaction(
                        user_telegram_id=user.telegram_id,
                        user_name=(user.display_name or "User")[:255],
                        amount=-CLAN_RENAME_DIAMOND_COST,
                        balance_after=user.diamonds,
                        action="rename_clan",
                        note=f"Clan nomi o'zgartirildi: {clan.name} -> {clean_name}",
                    )
                )

                old_name = clan.name
                clan.name = clean_name
                session.add(
                    ClanTransaction(
                        clan_id=clan.id,
                        user_telegram_id=owner_telegram_id,
                        type="name_change",
                        currency="diamonds",
                        amount=CLAN_RENAME_DIAMOND_COST,
                        description=f"Nom o'zgartirildi: {old_name} -> {clean_name}",
                    )
                )
                session.add(
                    ClanAuditLog(
                        clan_id=clan.id,
                        actor_telegram_id=owner_telegram_id,
                        action="change_name",
                        metadata_json=json.dumps({"old": old_name, "new": clean_name}),
                    )
                )

        return True, f"✏️ Clan nomi <b>{clean_name}</b> ga o'zgartirildi!"

    async def change_clan_bio(self, owner_telegram_id: int, new_bio: str) -> tuple[bool, str]:
        clean_bio = (new_bio or "").strip()[:300]
        if not clean_bio:
            return False, "❌ Bio bo'sh bo'lishi mumkin emas."

        async with self.session_factory() as session:
            async with session.begin():
                clan = (
                    await session.execute(
                        select(Clan).where(Clan.owner_id == owner_telegram_id, Clan.status == "active")
                    )
                ).scalar_one_or_none()
                if clan is None:
                    return False, "🚫 Siz aktiv Clan owner'i emassiz."

                user = (
                    await session.execute(
                        select(User).where(User.telegram_id == owner_telegram_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if user is None or int(user.dollar or 0) < CLAN_BIO_DOLLAR_COST:
                    return False, f"❌ Balansingizda yetarli dollar yo'q. Kerak: <b>${CLAN_BIO_DOLLAR_COST:,}</b>."

                # Deduct
                user.dollar = int(user.dollar or 0) - CLAN_BIO_DOLLAR_COST
                session.add(
                    DollarTransaction(
                        user_telegram_id=user.telegram_id,
                        user_name=(user.display_name or "User")[:255],
                        amount=-CLAN_BIO_DOLLAR_COST,
                        balance_after=user.dollar,
                        action="change_clan_bio",
                        note=f"Clan bio o'zgartirildi: {clan.name}",
                    )
                )

                clan.bio = clean_bio
                session.add(
                    ClanTransaction(
                        clan_id=clan.id,
                        user_telegram_id=owner_telegram_id,
                        type="bio_change",
                        currency="dollar",
                        amount=CLAN_BIO_DOLLAR_COST,
                        description="Clan bio o'zgartirildi",
                    )
                )
                session.add(
                    ClanAuditLog(
                        clan_id=clan.id,
                        actor_telegram_id=owner_telegram_id,
                        action="change_bio",
                    )
                )

        return True, "📝 Clan bio'si muvaffaqiyatli o'zgartirildi!"

    async def delete_clan(self, owner_telegram_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            async with session.begin():
                clan = (
                    await session.execute(
                        select(Clan).where(Clan.owner_id == owner_telegram_id, Clan.status == "active")
                    )
                ).scalar_one_or_none()
                if clan is None:
                    return False, "🚫 Siz aktiv Clan owner'i emassiz."

                clan.status = "deleted"
                clan.deleted_at = self._now()

                # Cancel pending applications
                await session.execute(
                    select(ClanApplication)
                    .where(ClanApplication.clan_id == clan.id, ClanApplication.status == "pending")
                )
                await session.execute(
                    select(ClanMember).where(ClanMember.clan_id == clan.id)
                )

                session.add(
                    ClanAuditLog(
                        clan_id=clan.id,
                        actor_telegram_id=owner_telegram_id,
                        action="delete_clan",
                    )
                )

        return True, f"🗑 <b>{clan.name}</b> Clani muvaffaqiyatli o'chirildi."

    async def award_clan_game_xp(
        self, game_id: int, player_results: list[tuple[int, bool]]
    ) -> None:
        """
        Idempotent game end reward processor.
        player_results: [(telegram_id, won_boolean), ...]
        """
        if not player_results:
            return

        async with self.session_factory() as session:
            async with session.begin():
                for telegram_id, won in player_results:
                    # Check idempotency per game per player
                    already_rewarded = (
                        await session.execute(
                            select(ClanGameReward.id).where(
                                ClanGameReward.game_id == game_id,
                                ClanGameReward.player_telegram_id == telegram_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if already_rewarded is not None:
                        continue

                    member = (
                        await session.execute(
                            select(ClanMember)
                            .join(Clan, ClanMember.clan_id == Clan.id)
                            .where(ClanMember.user_telegram_id == telegram_id, Clan.status == "active")
                        )
                    ).scalar_one_or_none()
                    if member is None:
                        continue

                    clan = await session.get(Clan, member.clan_id)
                    if clan is None or clan.status != "active":
                        continue

                    xp_awarded = WIN_XP if won else LOSS_XP
                    result_str = "win" if won else "loss"

                    # Record reward
                    session.add(
                        ClanGameReward(
                            game_id=game_id,
                            clan_id=clan.id,
                            player_telegram_id=telegram_id,
                            result=result_str,
                            xp_awarded=xp_awarded,
                        )
                    )

                    # Update member stats
                    member.personal_games += 1
                    member.contribution_xp += xp_awarded
                    if won:
                        member.personal_wins += 1
                    else:
                        member.personal_losses += 1

                    # Update clan stats
                    clan.total_games += 1
                    clan.xp += xp_awarded
                    if won:
                        clan.wins += 1
                    else:
                        clan.losses += 1

                    # Level Up check
                    next_req = xp_for_level(clan.level)
                    while clan.xp >= next_req:
                        clan.level += 1
                        clan.member_limit = calc_member_limit(clan.level)
                        next_req = xp_for_level(clan.level)

                    # Member count
                    m_count = int(
                        await session.scalar(
                            select(func.count(ClanMember.id)).where(ClanMember.clan_id == clan.id)
                        )
                        or 1
                    )
                    clan.rating = calc_clan_power(clan.wins, clan.total_games, clan.xp, clan.level, m_count)

    async def get_top_clans(self, limit: int = 10, page: int = 1) -> tuple[list[Clan], int]:
        async with self.session_factory() as session:
            offset = max(0, (page - 1) * limit)
            total = int(
                await session.scalar(
                    select(func.count(Clan.id)).where(Clan.status == "active")
                )
                or 0
            )
            clans = (
                await session.execute(
                    select(Clan)
                    .where(Clan.status == "active")
                    .order_by(Clan.rating.desc(), Clan.level.desc(), Clan.xp.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars().all()
            return list(clans), total

    async def get_pending_applications(self, clan_id: int) -> list[tuple[ClanApplication, User]]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(ClanApplication, User)
                    .join(User, ClanApplication.user_telegram_id == User.telegram_id)
                    .where(ClanApplication.clan_id == clan_id, ClanApplication.status == "pending")
                    .order_by(ClanApplication.created_at.asc())
                )
            ).all()
            return [(app, usr) for app, usr in rows]

    async def get_clan_members(self, clan_id: int, page: int = 1, limit: int = 10) -> tuple[list[tuple[ClanMember, User]], int]:
        async with self.session_factory() as session:
            offset = max(0, (page - 1) * limit)
            total = int(
                await session.scalar(
                    select(func.count(ClanMember.id)).where(ClanMember.clan_id == clan_id)
                )
                or 0
            )
            rows = (
                await session.execute(
                    select(ClanMember, User)
                    .join(User, ClanMember.user_telegram_id == User.telegram_id)
                    .where(ClanMember.clan_id == clan_id)
                    .order_by(
                        ClanMember.role == "owner",
                        ClanMember.contribution_xp.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
            return [(m, u) for m, u in rows], total

    async def search_clans(self, query: str, page: int = 1, limit: int = 10) -> tuple[list[Clan], int]:
        clean_q = f"%{query.strip().lower()}%"
        async with self.session_factory() as session:
            offset = max(0, (page - 1) * limit)
            total = int(
                await session.scalar(
                    select(func.count(Clan.id)).where(
                        Clan.status == "active",
                        func.lower(Clan.name).like(clean_q),
                    )
                )
                or 0
            )
            clans = (
                await session.execute(
                    select(Clan)
                    .where(Clan.status == "active", func.lower(Clan.name).like(clean_q))
                    .order_by(Clan.rating.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars().all()
            return list(clans), total

    async def get_my_applications(self, telegram_id: int) -> list[tuple[ClanApplication, Clan]]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(ClanApplication, Clan)
                    .join(Clan, ClanApplication.clan_id == Clan.id)
                    .where(ClanApplication.user_telegram_id == telegram_id, ClanApplication.status == "pending")
                    .order_by(ClanApplication.created_at.desc())
                )
            ).all()
            return [(app, clan) for app, clan in rows]

    async def admin_delete_clan(self, admin_telegram_id: int, clan_id: int, reason: str = "Admin decision") -> tuple[bool, str]:
        async with self.session_factory() as session:
            async with session.begin():
                clan = await session.get(Clan, clan_id)
                if clan is None or clan.status != "active":
                    return False, "❌ Clan topilmadi yoki allaqachon o'chirilgan."

                clan.status = "deleted"
                clan.deleted_at = self._now()

                session.add(
                    ClanAuditLog(
                        clan_id=clan.id,
                        actor_telegram_id=admin_telegram_id,
                        action="admin_delete_clan",
                        metadata_json=json.dumps({"reason": reason}),
                    )
                )

        return True, f"🛡 <b>{clan.name}</b> Clani admin tomonidan o'chirildi."
