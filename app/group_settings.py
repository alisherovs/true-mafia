from __future__ import annotations

from typing import Optional
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    GroupSettings,
    GroupRoleSettings,
    GroupWeaponSettings,
    GroupCommandPermissions,
    GroupChatPermissions,
    GroupTimeSettings,
    GroupExtraSettings,
)

ROLE_KEYS = [
    "don", "mafia", "commissar_katani", "doctor", "sergeant", "gentleman",
    "citizen", "wanderer", "traveler", "lawyer", "suicide", "lucky",
    "wolf", "killer", "mercenary_killer", "sorcerer", "swindler",
    "magician", "angry", "journalist", "traitor", "chemist", "guard", "joker",
]

WEAPON_KEYS = [
    "protection", "document", "killer_protection", "vote_protection",
    "gun", "medicine_protection", "slip_protection", "mask", "hero", "active_role",
]

COMMAND_KEYS = [
    "start", "stop", "game", "top_1", "top_7", "top_30",
    "reward_top_1", "reward_top_7", "reward_top_30",
]

TIME_KEYS = ["night_time", "day_time", "vote_time", "registration_time"]

EXTRA_KEYS = ["notifications", "auto_clean", "pin_message", "result_announce", "admin_start_confirm"]

DEFAULT_COMMAND_PERMISSIONS = {
    "start": "user", "stop": "admin", "game": "user",
    "top_1": "user", "top_7": "user", "top_30": "user",
    "reward_top_1": "admin", "reward_top_7": "admin", "reward_top_30": "admin",
}

DEFAULT_TIME_SECONDS = {
    "night_time": 60, "day_time": 120, "vote_time": 60, "registration_time": 120,
}

DEFAULT_EXTRA = {
    "notifications": True, "auto_clean": False, "pin_message": False, "result_announce": True,
    "admin_start_confirm": False,
}


class GroupSettingsManager:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        self._defaults_initialized: set[int] = set()

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)

    async def ensure_defaults(self, chat_id: int) -> None:
        if chat_id in self._defaults_initialized:
            return
        async with self.session_factory() as session:
            gs = await session.get(GroupSettings, chat_id)
            if gs is None:
                session.add(GroupSettings(chat_id=chat_id))
            for rk in ROLE_KEYS:
                existing = await session.execute(
                    select(GroupRoleSettings.id).where(
                        GroupRoleSettings.chat_id == chat_id,
                        GroupRoleSettings.role_key == rk,
                    )
                )
                if existing.scalar_one_or_none() is None:
                    session.add(GroupRoleSettings(chat_id=chat_id, role_key=rk, is_allowed=True))
            for wk in WEAPON_KEYS:
                existing = await session.execute(
                    select(GroupWeaponSettings.id).where(
                        GroupWeaponSettings.chat_id == chat_id,
                        GroupWeaponSettings.weapon_key == wk,
                    )
                )
                if existing.scalar_one_or_none() is None:
                    session.add(GroupWeaponSettings(chat_id=chat_id, weapon_key=wk, is_enabled=True))
            for ck in COMMAND_KEYS:
                existing = await session.execute(
                    select(GroupCommandPermissions.id).where(
                        GroupCommandPermissions.chat_id == chat_id,
                        GroupCommandPermissions.command_key == ck,
                    )
                )
                if existing.scalar_one_or_none() is None:
                    session.add(GroupCommandPermissions(
                        chat_id=chat_id, command_key=ck,
                        permission_level=DEFAULT_COMMAND_PERMISSIONS.get(ck, "user"),
                    ))
            for phase in ("night", "day"):
                existing = await session.execute(
                    select(GroupChatPermissions.id).where(
                        GroupChatPermissions.chat_id == chat_id,
                        GroupChatPermissions.phase == phase,
                    )
                )
                if existing.scalar_one_or_none() is None:
                    session.add(GroupChatPermissions(
                        chat_id=chat_id, phase=phase,
                        write_permission="alive_players" if phase == "night" else "all",
                    ))
            for tk in TIME_KEYS:
                existing = await session.execute(
                    select(GroupTimeSettings.id).where(
                        GroupTimeSettings.chat_id == chat_id,
                        GroupTimeSettings.time_key == tk,
                    )
                )
                if existing.scalar_one_or_none() is None:
                    session.add(GroupTimeSettings(
                        chat_id=chat_id, time_key=tk,
                        seconds=DEFAULT_TIME_SECONDS.get(tk, 60),
                    ))
            for ek in EXTRA_KEYS:
                existing = await session.execute(
                    select(GroupExtraSettings.id).where(
                        GroupExtraSettings.chat_id == chat_id,
                        GroupExtraSettings.setting_key == ek,
                    )
                )
                if existing.scalar_one_or_none() is None:
                    session.add(GroupExtraSettings(
                        chat_id=chat_id, setting_key=ek,
                        is_enabled=DEFAULT_EXTRA.get(ek, True),
                    ))
            await session.commit()
            self._defaults_initialized.add(chat_id)

    async def get_settings(self, chat_id: int) -> GroupSettings:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            gs = await session.get(GroupSettings, chat_id)
            return gs

    async def set_giveaway_diamond(self, chat_id: int, amount: int) -> None:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            gs = await session.get(GroupSettings, chat_id)
            gs.giveaway_diamond = amount
            await session.commit()

    async def set_giveaway_protection(self, chat_id: int, amount: int) -> None:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            gs = await session.get(GroupSettings, chat_id)
            gs.giveaway_protection = amount
            await session.commit()

    async def set_leave_allowed(self, chat_id: int, allowed: bool) -> None:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            gs = await session.get(GroupSettings, chat_id)
            gs.leave_allowed = allowed
            await session.commit()

    async def set_game_mode(self, chat_id: int, mode: str) -> None:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            gs = await session.get(GroupSettings, chat_id)
            gs.game_mode = mode
            await session.commit()

    async def get_role_allowed(self, chat_id: int, role_key: str) -> bool:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            row = await session.execute(
                select(GroupRoleSettings).where(
                    GroupRoleSettings.chat_id == chat_id,
                    GroupRoleSettings.role_key == role_key,
                )
            )
            result = row.scalar_one_or_none()
            return result.is_allowed if result else True

    async def set_role_allowed(self, chat_id: int, role_key: str, allowed: bool) -> None:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            row = await session.execute(
                select(GroupRoleSettings).where(
                    GroupRoleSettings.chat_id == chat_id,
                    GroupRoleSettings.role_key == role_key,
                )
            )
            result = row.scalar_one_or_none()
            if result:
                result.is_allowed = allowed
            await session.commit()

    async def get_disabled_roles(self, chat_id: int) -> set[str]:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            rows = await session.execute(
                select(GroupRoleSettings).where(
                    GroupRoleSettings.chat_id == chat_id,
                    GroupRoleSettings.is_allowed.is_(False),
                )
            )
            return {row.role_key for row in rows.scalars().all()}

    async def get_weapon_enabled(self, chat_id: int, weapon_key: str) -> bool:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            row = await session.execute(
                select(GroupWeaponSettings).where(
                    GroupWeaponSettings.chat_id == chat_id,
                    GroupWeaponSettings.weapon_key == weapon_key,
                )
            )
            result = row.scalar_one_or_none()
            return result.is_enabled if result else True

    async def set_weapon_enabled(self, chat_id: int, weapon_key: str, enabled: bool) -> None:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            row = await session.execute(
                select(GroupWeaponSettings).where(
                    GroupWeaponSettings.chat_id == chat_id,
                    GroupWeaponSettings.weapon_key == weapon_key,
                )
            )
            result = row.scalar_one_or_none()
            if result:
                result.is_enabled = enabled
            await session.commit()

    async def get_command_permission(self, chat_id: int, command_key: str) -> str:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            row = await session.execute(
                select(GroupCommandPermissions).where(
                    GroupCommandPermissions.chat_id == chat_id,
                    GroupCommandPermissions.command_key == command_key,
                )
            )
            result = row.scalar_one_or_none()
            return result.permission_level if result else "user"

    async def set_command_permission(self, chat_id: int, command_key: str, level: str) -> None:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            row = await session.execute(
                select(GroupCommandPermissions).where(
                    GroupCommandPermissions.chat_id == chat_id,
                    GroupCommandPermissions.command_key == command_key,
                )
            )
            result = row.scalar_one_or_none()
            if result:
                result.permission_level = level
            await session.commit()

    async def get_chat_permission(self, chat_id: int, phase: str) -> str:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            row = await session.execute(
                select(GroupChatPermissions).where(
                    GroupChatPermissions.chat_id == chat_id,
                    GroupChatPermissions.phase == phase,
                )
            )
            result = row.scalar_one_or_none()
            return result.write_permission if result else ("alive_players" if phase == "night" else "all")

    async def set_chat_permission(self, chat_id: int, phase: str, permission: str) -> None:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            row = await session.execute(
                select(GroupChatPermissions).where(
                    GroupChatPermissions.chat_id == chat_id,
                    GroupChatPermissions.phase == phase,
                )
            )
            result = row.scalar_one_or_none()
            if result:
                result.write_permission = permission
            await session.commit()

    async def get_time_setting(self, chat_id: int, time_key: str) -> int:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            row = await session.execute(
                select(GroupTimeSettings).where(
                    GroupTimeSettings.chat_id == chat_id,
                    GroupTimeSettings.time_key == time_key,
                )
            )
            result = row.scalar_one_or_none()
            return result.seconds if result else DEFAULT_TIME_SECONDS.get(time_key, 60)

    async def set_time_setting(self, chat_id: int, time_key: str, seconds: int) -> None:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            row = await session.execute(
                select(GroupTimeSettings).where(
                    GroupTimeSettings.chat_id == chat_id,
                    GroupTimeSettings.time_key == time_key,
                )
            )
            result = row.scalar_one_or_none()
            if result:
                result.seconds = seconds
            await session.commit()

    async def get_extra_enabled(self, chat_id: int, setting_key: str) -> bool:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            row = await session.execute(
                select(GroupExtraSettings).where(
                    GroupExtraSettings.chat_id == chat_id,
                    GroupExtraSettings.setting_key == setting_key,
                )
            )
            result = row.scalar_one_or_none()
            return result.is_enabled if result else DEFAULT_EXTRA.get(setting_key, True)

    async def set_extra_enabled(self, chat_id: int, setting_key: str, enabled: bool) -> None:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            row = await session.execute(
                select(GroupExtraSettings).where(
                    GroupExtraSettings.chat_id == chat_id,
                    GroupExtraSettings.setting_key == setting_key,
                )
            )
            result = row.scalar_one_or_none()
            if result:
                result.is_enabled = enabled
            await session.commit()

    async def get_all_roles(self, chat_id: int) -> dict[str, bool]:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            rows = (await session.execute(
                select(GroupRoleSettings).where(GroupRoleSettings.chat_id == chat_id)
            )).scalars().all()
            return {r.role_key: r.is_allowed for r in rows}

    async def get_all_weapons(self, chat_id: int) -> dict[str, bool]:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            rows = (await session.execute(
                select(GroupWeaponSettings).where(GroupWeaponSettings.chat_id == chat_id)
            )).scalars().all()
            return {r.weapon_key: r.is_enabled for r in rows}

    async def get_all_command_permissions(self, chat_id: int) -> dict[str, str]:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            rows = (await session.execute(
                select(GroupCommandPermissions).where(GroupCommandPermissions.chat_id == chat_id)
            )).scalars().all()
            return {r.command_key: r.permission_level for r in rows}

    async def get_all_chat_permissions(self, chat_id: int) -> dict[str, str]:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            rows = (await session.execute(
                select(GroupChatPermissions).where(GroupChatPermissions.chat_id == chat_id)
            )).scalars().all()
            return {r.phase: r.write_permission for r in rows}

    async def get_all_time_settings(self, chat_id: int) -> dict[str, int]:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            rows = (await session.execute(
                select(GroupTimeSettings).where(GroupTimeSettings.chat_id == chat_id)
            )).scalars().all()
            return {r.time_key: r.seconds for r in rows}

    async def get_all_extra(self, chat_id: int) -> dict[str, bool]:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            rows = (await session.execute(
                select(GroupExtraSettings).where(GroupExtraSettings.chat_id == chat_id)
            )).scalars().all()
            return {r.setting_key: r.is_enabled for r in rows}

    async def get_panel_data(self, chat_id: int) -> dict:
        await self.ensure_defaults(chat_id)
        async with self.session_factory() as session:
            gs = await session.get(GroupSettings, chat_id)
            roles_enabled_count = len((await session.execute(
                select(GroupRoleSettings).where(
                    GroupRoleSettings.chat_id == chat_id,
                    GroupRoleSettings.is_allowed.is_(True),
                )
            )).scalars().all())
            roles_disabled_count = len((await session.execute(
                select(GroupRoleSettings).where(
                    GroupRoleSettings.chat_id == chat_id,
                    GroupRoleSettings.is_allowed.is_(False),
                )
            )).scalars().all())
            weapons_enabled_count = len((await session.execute(
                select(GroupWeaponSettings).where(
                    GroupWeaponSettings.chat_id == chat_id,
                    GroupWeaponSettings.is_enabled.is_(True),
                )
            )).scalars().all())
            weapons_disabled_count = len((await session.execute(
                select(GroupWeaponSettings).where(
                    GroupWeaponSettings.chat_id == chat_id,
                    GroupWeaponSettings.is_enabled.is_(False),
                )
            )).scalars().all())
            cmd_perms = {}
            for ck in COMMAND_KEYS:
                row = await session.execute(
                    select(GroupCommandPermissions).where(
                        GroupCommandPermissions.chat_id == chat_id,
                        GroupCommandPermissions.command_key == ck,
                    )
                )
                r = row.scalar_one_or_none()
                cmd_perms[ck] = r.permission_level if r else "user"
            night_perm = await self.get_chat_permission(chat_id, "night")
            day_perm = await self.get_chat_permission(chat_id, "day")

        return {
            "giveaway_diamond": gs.giveaway_diamond,
            "giveaway_protection": gs.giveaway_protection,
            "leave_allowed": gs.leave_allowed,
            "game_mode": gs.game_mode,
            "roles_enabled": roles_enabled_count,
            "roles_disabled": roles_disabled_count,
            "weapons_enabled": weapons_enabled_count,
            "weapons_disabled": weapons_disabled_count,
            "command_permissions": cmd_perms,
            "night_permission": night_perm,
            "day_permission": day_perm,
        }
