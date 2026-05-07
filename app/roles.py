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
    Role.CITIZEN: RoleMeta(
        Role.CITIZEN,
        Team.CITY,
        "👨🏼",
        "Tinch axoli",
        "Sizning vazifangiz mafiani topish va ovoz berish jarayonida ularni osish",
    ),
    Role.MISTRESS: RoleMeta(
        Role.MISTRESS,
        Team.CITY,
        "💃",
        "Kezuvchi",
        "Bu shavqatsiz shaxarda tirik qolishingiz kerak. Siz mehmonga borgan odamingizga uyqu     💊dori berasiz va u bir kun uxlaydi :)",
    ),
    Role.SERGEANT: RoleMeta(
        Role.SERGEANT,
        Team.CITY,
        "👮🏼",
        "Serjant",
        "🕵🏻‍♂ Komissar Katanining yordamchisi. U sizni o'zining qilayotgan ishlaridan xabardor qilib turadi. Agar komissar o'lsa uning o'rnini siz egallaysiz.",
    ),
    Role.COMMISSAR: RoleMeta(
        Role.COMMISSAR,
        Team.CITY,
        "🕵🏼",
        "Komissar katani",
        "Shaharning asosiy ximoyachisi va mafia kushandasi...",
    ),
    Role.DOCTOR: RoleMeta(
        Role.DOCTOR,
        Team.CITY,
        "👨🏼‍⚕️️",
        "Doktor",
        "Tunda kimnidir qutqarib qolishingiz mumkin...",
    ),
    Role.GUARD: RoleMeta(
        Role.GUARD,
        Team.CITY,
        "🛡",
        "Qo'riqchi",
        "Tunda bitta o'yinchini himoya qilasiz va uning hayotini saqlab qolishingiz mumkin.",
    ),
    Role.WATCHER: RoleMeta(
        Role.WATCHER,
        Team.CITY,
        "🔎",
        "Kuzatuvchi",
        "Tunda bir odamni kuzatasiz va uning oldiga kim kelganini bilib olasiz.",
    ),
    Role.JUDGE: RoleMeta(
        Role.JUDGE,
        Team.CITY,
        "🧑‍⚖️",
        "Sudya",
        "Kunduzgi osish hukmini o'yinda bir marta bekor qilishingiz mumkin.",
    ),
    Role.BUM: RoleMeta(
        Role.BUM,
        Team.CITY,
        "🧙‍♂️",
        "Daydi",
        "Siz xohlagan odamning uyiga ichkilik butilka olish uchun borishingiz va qotillikning guvoxi bo'lib qolishingiz mumkin.",
    ),
    Role.SORCERER: RoleMeta(
        Role.SORCERER,
        Team.CITY,
        "💣",
        "️Afsungar",
        "Sizning maqsadingiz tinch fuqarolarga yordam berish.  Agar kechasi o'ldirilsang, seni o'ldirgan ham o'ladi.  Agar kunlik ovoz berishda o'ldirilsangiz, siz biron bir o'yinchini tanlashingiz va uni o'zingiz bilan birga jahannaga ravona bo'lishingiz mumkin.",
    ),
    Role.DON: RoleMeta(
        Role.DON,
        Team.MAFIA,
        "🤵🏻",
        "Don",
        "Bu tunda kim o'lishini siz xal qilasiz. Siz (Mafialar sardori)siz.",
    ),
    Role.MAFIA: RoleMeta(
        Role.MAFIA,
        Team.MAFIA,
        "🤵🏼",
        "Mafia",
        "Siz Mafiasiz, Donga bo'ysunasiz va sizga qarshilik qilganlarni o'ldirasiz. Don o'lsa siz yangi Don bo'lishingiz mumkin.",
    ),
    Role.LAWYER: RoleMeta(
        Role.LAWYER,
        Team.MAFIA,
        "👨🏼‍💼",
        "Advokat",
        "Tunda kimni ximoya qilishni tanlaysiz. Agar siz mafiani tanlasangiz,  🕵 Komissar Katani uni taniy olmaydi va unga 👨🏼Tinch axoli bo'lib ko'rinadi. Siz mafia tarafdasiz.",
    ),
    Role.SPY: RoleMeta(
        Role.SPY,
        Team.MAFIA,
        "🕴",
        "Josus",
        "Mafialar tarafida yashirin o'ynaysiz. Komissar sizni tekshirsa, oddiy shahar odamidek ko'rinasiz.",
    ),
    Role.KILLER: RoleMeta(
        Role.KILLER,
        Team.KILLER,
        "🔪",
        "Qotil",
        "Shaxardagi xamma o'lishi kerak, sizdan tashqari albatta :)",
    ),
    Role.WOLF: RoleMeta(
        Role.WOLF,
        Team.KILLER,
        "🐺",
        "Bo'ri",
        "Agar Mafiya sizni o'ldirsa, unda siz kelgusi tunda mafiya bo'lasiz.  Komissar sizni o'ldirsa, siz serjantga aylanasiz. Qotil sizni o'ldirsa siz shu zahoti o'lasiz...",
    ),
    Role.JESTER: RoleMeta(
        Role.JESTER,
        Team.NEUTRAL,
        "🤦🏼",
        "Suidsid",
        "Seni osib o'ldirishsa sen yutasan! :)",
    ),
}


def role_label(role: Union[Role, str]) -> str:
    role_enum = role if isinstance(role, Role) else Role(role)
    meta = ROLE_META[role_enum]
    return f"{meta.emoji} {meta.title_uz}"


def role_team(role: Union[Role, str]) -> Team:
    role_enum = role if isinstance(role, Role) else Role(role)
    return ROLE_META[role_enum].team


C = Role.CITIZEN
M = Role.MAFIA

EXTENDED_ROLE_TABLE: dict[int, list[Role]] = {
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

ROLE_PRESET_LABELS = {
    "black23": "Black 23",
    "extended35": "Extended 35",
}

ROLE_PRESET_MAX_PLAYERS = {
    "black23": 23,
    "extended35": 35,
}


def role_preset_label(preset: str) -> str:
    return ROLE_PRESET_LABELS.get(preset, ROLE_PRESET_LABELS["black23"])


def role_preset_max_players(preset: str) -> int:
    return ROLE_PRESET_MAX_PLAYERS.get(preset, ROLE_PRESET_MAX_PLAYERS["black23"])


def build_role_set(player_count: int, preset: str = "black23") -> list[Role]:
    max_players = role_preset_max_players(preset)
    capped_count = min(player_count, max_players)
    if capped_count <= 35:
        roles = EXTENDED_ROLE_TABLE.get(capped_count, EXTENDED_ROLE_TABLE[4]).copy()
    else:
        roles = EXTENDED_ROLE_TABLE[35].copy()

    if player_count > len(roles):
        extras = [Role.CITIZEN, Role.CITIZEN, Role.MAFIA, Role.CITIZEN]
        idx = 0
        while len(roles) < player_count:
            roles.append(extras[idx % len(extras)])
            idx += 1

    shuffle(roles)
    return roles[:player_count]
