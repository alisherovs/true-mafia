from __future__ import annotations

from typing import Union
from dataclasses import dataclass
from random import shuffle

from app.enums import Role, Team


@dataclass(frozen=True)
class RoleMeta:
    role: Role
    team: Team
    emoji: str
    title_uz: str
    short_desc_uz: str


ROLE_META: dict[Role, RoleMeta] = {
    Role.CITIZEN: RoleMeta(Role.CITIZEN, Team.CITY, "👨🏼", "Tinch aholi", "Mafiyani topib, ovoz bilan chiqaradi."),
    Role.MISTRESS: RoleMeta(Role.MISTRESS, Team.CITY, "💃", "Kezuvchi", "Tunda bitta o'yinchini block qiladi."),
    Role.SERGEANT: RoleMeta(Role.SERGEANT, Team.CITY, "👮🏻‍♂", "Serjant", "Komissarga yordam beradi."),
    Role.COMMISSAR: RoleMeta(Role.COMMISSAR, Team.CITY, "🕵🏻‍♂", "Komissar Katani", "Mafiyani tekshiradi, keyin otishi mumkin."),
    Role.DOCTOR: RoleMeta(Role.DOCTOR, Team.CITY, "👨🏻‍⚕", "Doktor", "Tunda bitta o'yinchini davolaydi."),
    Role.GUARD: RoleMeta(Role.GUARD, Team.CITY, "🛡", "Qo'riqchi", "Tunda bitta o'yinchini hujumdan himoya qiladi."),
    Role.WATCHER: RoleMeta(Role.WATCHER, Team.CITY, "🔎", "Kuzatuvchi", "Bitta o'yinchiga kim kelganini kuzatadi."),
    Role.JUDGE: RoleMeta(Role.JUDGE, Team.CITY, "🧑‍⚖️", "Sudya", "O'yinda bir marta kunduzgi osishni bekor qiladi."),
    Role.BUM: RoleMeta(Role.BUM, Team.CITY, "🧙‍♂", "Daydi", "Bitta o'yinchini kuzatadi."),
    Role.SORCERER: RoleMeta(Role.SORCERER, Team.CITY, "🧞‍♂️", "Afsungar", "Revenge mexanika."),
    Role.DON: RoleMeta(Role.DON, Team.MAFIA, "🤵🏻", "Don", "Mafiya zarbasini boshqaradi."),
    Role.MAFIA: RoleMeta(Role.MAFIA, Team.MAFIA, "🤵🏼", "Mafiya", "Don bilan tunda nishon tanlaydi."),
    Role.LAWYER: RoleMeta(Role.LAWYER, Team.MAFIA, "👨‍💼", "Advokat", "Tekshiruvni yashiradi."),
    Role.SPY: RoleMeta(Role.SPY, Team.MAFIA, "🕴", "Josus", "Komissarga oddiy shahar odamidek ko'rinadi."),
    Role.KILLER: RoleMeta(Role.KILLER, Team.KILLER, "🔪", "Qotil", "Faqat o'zi qolsa yutadi."),
    Role.WOLF: RoleMeta(Role.WOLF, Team.KILLER, "🐺", "Bo'ri", "Holatga qarab transform bo'ladi."),
    Role.JESTER: RoleMeta(Role.JESTER, Team.NEUTRAL, "🎭", "Masxaraboz", "Kunduzgi ovoz bilan chiqarilsa alohida g'olib bo'ladi."),
}


def role_label(role: Union[Role, str]) -> str:
    role_enum = role if isinstance(role, Role) else Role(role)
    meta = ROLE_META[role_enum]
    return f"{meta.emoji} {meta.title_uz}"


def role_team(role: Union[Role, str]) -> Team:
    role_enum = role if isinstance(role, Role) else Role(role)
    return ROLE_META[role_enum].team


def build_role_set(player_count: int) -> list[Role]:
    C = Role.CITIZEN
    M = Role.MAFIA
    role_table: dict[int, list[Role]] = {
        4: [Role.DON, Role.DOCTOR, Role.CITIZEN, Role.CITIZEN],
        5: [Role.DON, Role.DOCTOR, C, C, C],
        6: [Role.DON, Role.DOCTOR, Role.COMMISSAR, C, C, C],
        7: [Role.DON, M, Role.DOCTOR, Role.COMMISSAR, C, C, C],
        8: [Role.DON, M, Role.DOCTOR, Role.COMMISSAR, Role.MISTRESS, C, C, C],
        9: [Role.DON, M, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, C, C, C],
        10: [Role.DON, M, M, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, C, C, C],
        11: [Role.DON, M, M, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, C, C, C],
        12: [Role.DON, M, M, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, C, C, C],
        13: [Role.DON, M, M, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, C, C, C],
        14: [Role.DON, M, M, Role.LAWYER, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, C, C, C],
        15: [Role.DON, M, M, Role.LAWYER, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.KILLER, C, C, C],
        16: [Role.DON, M, M, M, Role.LAWYER, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.KILLER, C, C, C],
        17: [Role.DON, M, M, M, Role.LAWYER, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.KILLER, C, C, C],
        18: [Role.DON, M, M, M, Role.LAWYER, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.KILLER, C, C, C, C],
        19: [Role.DON, M, M, M, Role.LAWYER, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.KILLER, C, C, C, C, C],
        20: [Role.DON, M, M, M, Role.LAWYER, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.KILLER, Role.WOLF, C, C, C, C, C],
        21: [Role.DON, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.KILLER, Role.WOLF, C, C, C, C, C],
        22: [Role.DON, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.KILLER, Role.WOLF, C, C, C, C, C],
        23: [Role.DON, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, C, C, C, C, C],
        24: [Role.DON, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, C, C, C, C, C, C],
        25: [Role.DON, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, Role.JESTER, C, C, C, C, C, C],
        26: [Role.DON, M, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, Role.JESTER, C, C, C, C, C, C],
        27: [Role.DON, M, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, Role.JESTER, C, C, C, C, C, C, C],
        28: [Role.DON, M, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, Role.JESTER, C, C, C, C, C, C, C, C],
        29: [Role.DON, M, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, Role.JESTER, C, C, C, C, C, C, C, C, C],
        30: [Role.DON, M, M, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, Role.JESTER, C, C, C, C, C, C, C, C, C],
        31: [Role.DON, M, M, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, Role.JESTER, C, C, C, C, C, C, C, C, C, C],
        32: [Role.DON, M, M, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, Role.JESTER, C, C, C, C, C, C, C, C, C, C, C],
        33: [Role.DON, M, M, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, Role.JESTER, C, C, C, C, C, C, C, C, C, C, C, C],
        34: [Role.DON, M, M, M, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, Role.JESTER, C, C, C, C, C, C, C, C, C, C, C, C],
        35: [Role.DON, M, M, M, M, M, M, M, Role.LAWYER, Role.SPY, Role.DOCTOR, Role.COMMISSAR, Role.SERGEANT, Role.MISTRESS, Role.BUM, Role.SORCERER, Role.GUARD, Role.WATCHER, Role.JUDGE, Role.KILLER, Role.WOLF, Role.JESTER, C, C, C, C, C, C, C, C, C, C, C, C, C],
    }

    if player_count <= 35:
        roles = role_table.get(player_count, role_table[4]).copy()
    else:
        roles = role_table[35].copy()
        extras = [Role.CITIZEN, Role.CITIZEN, Role.MAFIA, Role.CITIZEN]
        idx = 0
        while len(roles) < player_count:
            roles.append(extras[idx % len(extras)])
            idx += 1

    shuffle(roles)
    return roles[:player_count]
