from __future__ import annotations

from typing import Callable, Optional, Union
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


@dataclass(frozen=True)
class RoleShopItem:
    role: Role
    price: int
    currency: str


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
        "Afsungar",
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
        "Ayg'oqchi",
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
    Role.LUCKY: RoleMeta(
        Role.LUCKY,
        Team.CITY,
        "🤞🏼",
        "Omadli",
        "Qolganlaridan ko'ra omadli bo'lgan tinch axoli - hayotiga suiqasd bo'lsa, u omon qolishi mumkin omadi kulsa:)",
    ),
    Role.ARSONIST: RoleMeta(
        Role.ARSONIST,
        Team.KILLER,
        "🧟",
        "G'azabkor",
        "Har tunda 1 ta o'yinchini tanlaysiz. Agar o'zingizni tanlasangiz, oxirgi tunlarda tanlaganlariz bilan o'lasiz. Siz kamida 3 kishini tanlasangiz ģalaba qozonasiz..",
    ),
    Role.JOURNALIST: RoleMeta(
        Role.JOURNALIST,
        Team.MAFIA,
        "👩🏼‍💻",
        "Jurnalist",
        "Mafialarning agentisiz. Har tunda kimnikigadur interyu olishga borasiz va unga kelgan har bir o'yinchini ko'rib qolishingiz mumkin hamda bu haqida mafialarga xabar berasiz.",
    ),
    Role.SNITCH: RoleMeta(
        Role.SNITCH,
        Team.CITY,
        "🤓",
        "Sotqin",
        "Siz tunda bir odamni tanlaysiz va agarda u don, mafia yoki qotil bo'lsa, uni odamlarga shaxsingizni ochiqlamasdan sota olasiz! Siz tinch tarafda o'ynaysiz va tirik qolangiz yutasiz!",
    ),
    Role.MAYOR: RoleMeta(
        Role.MAYOR,
        Team.CITY,
        "🎖",
        "Janob",
        "Kunduzgi ovoz berishda sizning ovozingiz ikkitaga teng bo'ladi va ovoz berish payti shaxsingiz oshkor bo'lmaydi.",
    ),
    Role.CROOK: RoleMeta(
        Role.CROOK,
        Team.NEUTRAL,
        "🤹🏻",
        "Aferist",
        "Kechasi biron bir o'yinchiga tashrif buyurib, u bir kunlik ovoz berish uchun o'z ismini aldab qo'yishi mumkin.",
    ),
    Role.HIRED_KILLER: RoleMeta(
        Role.HIRED_KILLER,
        Team.MAFIA,
        "🥷",
        "Yollanma qotil",
        "Mafialar tarafda o'ynaysiz! Har tun kimnidir yashirincha ovlaydi; ammo agar komissarni nishonga olsa, komissar uni o'ldiradi.",
    ),
    Role.MAQ: RoleMeta(
        Role.MAQ,
        Team.NEUTRAL,
        "🧙‍♂️",
        "Sehrgar",
        "O'z qonunlaringiz bilan yashaysiz! Agar Don, Qotil, Komissar katani sizni o'ldirmoqchi bo'lsa, bu urinish behuda bo'ladi va sizga tanlov beriladi: Ularga rahm qilish yoki o'ldirish.",
    ),
    Role.MINER: RoleMeta(
        Role.MINER,
        Team.NEUTRAL,
        "👷🏻‍♂️",
        "Konchi",
        "Siz tunda 10 ta kondan birini tanlaysiz. U yerda 3 ta o'limli, 2 ta <tg-emoji emoji-id=\"5427168083074628963\">💎</tg-emoji>'li va 5 ta <tg-emoji emoji-id=\"5409048419211682843\">💵</tg-emoji>'li kon bor. Siz tanlagan koningizdagi narsani olasiz. Oxirigacha tirik qolsangiz yutasiz.",
    ),
    Role.PRANKSTER: RoleMeta(
        Role.PRANKSTER,
        Team.NEUTRAL,
        "🃏",
        "Joker",
        "Har tunda 4 kartadan birini o'lim kartasi qilib belgilaysiz va nishonga yuborasiz. Agar nishon o'lim kartasini tanlasa, u o'ladi.",
    ),
    Role.HOJIAKA: RoleMeta(
        Role.HOJIAKA,
        Team.CITY,
        "🕌",
        "Hojiaka",
        "Har tunda bir o'yinchiga ehson qilasiz: himoya buyumi, dollar yoki kamdan-kam olmos.",
    ),
    Role.MASHKA: RoleMeta(
        Role.MASHKA,
        Team.CITY,
        "🧤",
        "Mashka",
        "Har tunda bitta o'yinchidan pul o'g'irlaysiz: ko'proq dollar, kamdan-kam 1 ta olmos.",
    ),
}


SHOP_ROLE_CATALOG: tuple[RoleShopItem, ...] = (
    RoleShopItem(Role.PRANKSTER, 6, "diamonds"),
    RoleShopItem(Role.MINER, 5, "diamonds"),
    RoleShopItem(Role.HIRED_KILLER, 5, "diamonds"),
    RoleShopItem(Role.JUDGE, 4, "diamonds"),
    RoleShopItem(Role.SNITCH, 4, "diamonds"),
    RoleShopItem(Role.MAYOR, 3, "diamonds"),
    RoleShopItem(Role.MAQ, 3, "diamonds"),
    RoleShopItem(Role.COMMISSAR, 2, "diamonds"),
    RoleShopItem(Role.DON, 2, "diamonds"),
    RoleShopItem(Role.KILLER, 2, "diamonds"),
    RoleShopItem(Role.GUARD, 2, "diamonds"),
    RoleShopItem(Role.ARSONIST, 1, "diamonds"),
    RoleShopItem(Role.MAFIA, 1, "diamonds"),
    RoleShopItem(Role.SERGEANT, 1, "diamonds"),
    RoleShopItem(Role.CROOK, 1, "diamonds"),
    RoleShopItem(Role.WATCHER, 1, "diamonds"),
    RoleShopItem(Role.DOCTOR, 1000, "dollar"),
    RoleShopItem(Role.MISTRESS, 500, "dollar"),
    RoleShopItem(Role.JOURNALIST, 500, "dollar"),
    RoleShopItem(Role.LAWYER, 500, "dollar"),
    RoleShopItem(Role.SORCERER, 500, "dollar"),
    RoleShopItem(Role.HOJIAKA, 500, "dollar"),
    RoleShopItem(Role.MASHKA, 500, "dollar"),
    RoleShopItem(Role.WOLF, 500, "dollar"),
    RoleShopItem(Role.BUM, 400, "dollar"),
    RoleShopItem(Role.JESTER, 300, "dollar"),
    RoleShopItem(Role.SPY, 300, "dollar"),
    RoleShopItem(Role.LUCKY, 250, "dollar"),
    RoleShopItem(Role.CITIZEN, 100, "dollar"),
)

SHOP_ROLE_BY_VALUE: dict[str, RoleShopItem] = {
    item.role.value: item for item in SHOP_ROLE_CATALOG
}


def role_label(role: Union[Role, str]) -> str:
    role_enum = role if isinstance(role, Role) else Role(role)
    meta = ROLE_META[role_enum]
    return f"{meta.emoji} {meta.title_uz}"


def role_team(role: Union[Role, str]) -> Team:
    role_enum = role if isinstance(role, Role) else Role(role)
    return ROLE_META[role_enum].team


def _expand_role_counts(*counts: tuple[Role, int]) -> list[Role]:
    roles: list[Role] = []
    for role, count in counts:
        roles.extend([role] * count)
    return roles


C = Role.CITIZEN
M = Role.MAFIA
KAMIKAZE = Role.SORCERER
MANIAC = Role.KILLER
MER = Role.MAYOR
HIRED_KILLER = Role.HIRED_KILLER
MINER = Role.MINER
PRANKSTER = Role.PRANKSTER

BASE_ROLE_TABLE: dict[int, list[Role]] = {
    4: _expand_role_counts((Role.DON, 1), (Role.COMMISSAR, 1), (C, 2)),
    5: _expand_role_counts((Role.DON, 1), (Role.COMMISSAR, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (C, 1)),
    6: _expand_role_counts((Role.DON, 1), (M, 1), (Role.COMMISSAR, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (C, 1)),
    7: _expand_role_counts((Role.DON, 1), (M, 1), (Role.COMMISSAR, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (C, 1)),
    8: _expand_role_counts((Role.DON, 1), (M, 1), (Role.COMMISSAR, 1), (KAMIKAZE, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (C, 1)),
    9: _expand_role_counts((Role.DON, 1), (M, 2), (Role.COMMISSAR, 1), (KAMIKAZE, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (C, 1)),
    10: _expand_role_counts((Role.DON, 1), (M, 2), (Role.COMMISSAR, 1), (KAMIKAZE, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.WOLF, 1), (C, 1)),
    11: _expand_role_counts((Role.DON, 1), (M, 2), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.WOLF, 1), (C, 1)),
    12: _expand_role_counts((Role.DON, 1), (M, 3), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.WOLF, 1), (C, 1)),
    13: _expand_role_counts((Role.DON, 1), (M, 3), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.WOLF, 1), (C, 1), (Role.JESTER, 1)),
    14: _expand_role_counts((Role.DON, 1), (M, 3), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.WOLF, 1), (MANIAC, 1), (C, 1), (Role.JESTER, 1)),
    15: _expand_role_counts((Role.DON, 1), (M, 4), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 1), (MANIAC, 1), (C, 1)),
    16: _expand_role_counts((Role.DON, 1), (M, 4), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 1), (MANIAC, 1), (C, 1), (Role.JESTER, 1)),
    17: _expand_role_counts((Role.DON, 1), (M, 4), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 1), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1)),
    18: _expand_role_counts((Role.DON, 1), (M, 5), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 1), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 1), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1)),
    19: _expand_role_counts((Role.DON, 1), (M, 5), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 2), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 1), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1)),
    20: _expand_role_counts((Role.DON, 1), (M, 5), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 2), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 1), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1), (Role.MAQ, 1)),
    21: _expand_role_counts((Role.DON, 1), (M, 4), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 2), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 1), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1), (Role.ARSONIST, 1), (Role.JOURNALIST, 1), (Role.MAQ, 1)),
    22: _expand_role_counts((Role.DON, 1), (M, 4), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 2), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 2), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1), (Role.ARSONIST, 1), (Role.JOURNALIST, 1), (Role.MAQ, 1)),
    23: _expand_role_counts((Role.DON, 1), (M, 5), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 2), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 2), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1), (Role.ARSONIST, 1), (Role.JOURNALIST, 1), (Role.MAQ, 1)),
    24: _expand_role_counts((Role.DON, 1), (M, 5), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 2), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 2), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1), (Role.ARSONIST, 1), (Role.JOURNALIST, 1), (Role.MAQ, 1), (MER, 1)),
    25: _expand_role_counts((Role.DON, 1), (M, 6), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 2), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 2), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1), (Role.ARSONIST, 1), (Role.JOURNALIST, 1), (Role.MAQ, 1), (MER, 1)),
    26: _expand_role_counts((Role.DON, 1), (M, 6), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 2), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 2), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1), (Role.ARSONIST, 1), (Role.JOURNALIST, 1), (Role.MAQ, 1), (Role.SNITCH, 1), (MER, 1)),
    27: _expand_role_counts((Role.DON, 1), (M, 6), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 2), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 1), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1), (Role.ARSONIST, 1), (Role.JOURNALIST, 1), (Role.MAQ, 1), (Role.SNITCH, 1), (MER, 1), (Role.CROOK, 1), (HIRED_KILLER, 1)),
    28: _expand_role_counts((Role.DON, 1), (M, 6), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 3), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 1), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1), (Role.ARSONIST, 1), (Role.JOURNALIST, 1), (Role.MAQ, 1), (Role.SNITCH, 1), (MER, 1), (Role.CROOK, 1), (HIRED_KILLER, 1)),
    29: _expand_role_counts((Role.DON, 1), (M, 6), (Role.COMMISSAR, 1), (Role.SERGEANT, 1), (KAMIKAZE, 3), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 2), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1), (Role.ARSONIST, 1), (Role.JOURNALIST, 1), (Role.MAQ, 1), (Role.SNITCH, 1), (MER, 1), (Role.CROOK, 1), (HIRED_KILLER, 1)),
    30: _expand_role_counts((Role.DON, 1), (M, 6), (Role.COMMISSAR, 1), (Role.SERGEANT, 2), (KAMIKAZE, 3), (Role.DOCTOR, 1), (Role.MISTRESS, 1), (Role.BUM, 1), (Role.LAWYER, 1), (Role.WOLF, 2), (MANIAC, 1), (C, 1), (Role.LUCKY, 1), (Role.JESTER, 1), (Role.ARSONIST, 1), (Role.JOURNALIST, 1), (Role.MAQ, 1), (Role.SNITCH, 1), (MER, 1), (Role.CROOK, 1), (HIRED_KILLER, 1)),
}


def _with_miner(player_count: int, roles: list[Role]) -> list[Role]:
    if player_count <= 15 or Role.MINER in roles:
        return roles
    updated = list(roles)
    replacement_priority = [Role.CITIZEN, Role.JESTER, Role.LUCKY, Role.CROOK]
    for role in replacement_priority:
        if role in updated:
            updated[updated.index(role)] = Role.MINER
            return updated
    updated[-1] = Role.MINER
    return updated


def _with_prankster(player_count: int, roles: list[Role]) -> list[Role]:
    if player_count < 17 or Role.PRANKSTER in roles:
        return roles
    updated = list(roles)
    replacement_priority = [Role.CITIZEN, Role.JESTER, Role.LUCKY, Role.CROOK]
    for role in replacement_priority:
        if role in updated:
            updated[updated.index(role)] = Role.PRANKSTER
            return updated
    updated[-1] = Role.PRANKSTER
    return updated


def _with_hojiaka(player_count: int, roles: list[Role]) -> list[Role]:
    if player_count <= 14 or Role.HOJIAKA in roles:
        return roles
    updated = list(roles)
    replacement_priority = [Role.CITIZEN, Role.JESTER, Role.LUCKY, Role.CROOK]
    for role in replacement_priority:
        if role in updated:
            updated[updated.index(role)] = Role.HOJIAKA
            return updated
    updated[-1] = Role.HOJIAKA
    return updated


def _with_mashka(player_count: int, roles: list[Role]) -> list[Role]:
    if player_count <= 14 or Role.MASHKA in roles:
        return roles
    updated = list(roles)
    replacement_priority = [Role.CITIZEN, Role.JESTER, Role.LUCKY, Role.CROOK]
    for role in replacement_priority:
        if role in updated:
            updated[updated.index(role)] = Role.MASHKA
            return updated
    updated[-1] = Role.MASHKA
    return updated


EXTENDED_ROLE_TABLE: dict[int, list[Role]] = {
    count: _with_mashka(
        count,
        _with_hojiaka(
            count,
            _with_prankster(count, _with_miner(count, roles)),
        ),
    )
    for count, roles in BASE_ROLE_TABLE.items()
}

ROLE_PRESET_LABELS = {
    "classic": "Classic",
    "super": "Super",
    "mega": "Mega",
    "black23": "Universal 30",
    "extended35": "Universal 30",
}

ROLE_PRESET_MAX_PLAYERS = {
    "classic": 30,
    "super": 40,
    "mega": 40,
    "black23": 30,
    "extended35": 30,
}

GAME_MODE_CLASSIC = "classic"
GAME_MODE_SUPER = "super"
GAME_MODE_MEGA = "mega"
GAME_MODES = {GAME_MODE_CLASSIC, GAME_MODE_SUPER, GAME_MODE_MEGA}

ROLE_MIN_PLAYERS: dict[str, int] = {
    "don": 4,
    "commissar": 4,
    "doctor": 5,
    "mafia": 8,
    "advocate": 8,
    "spy": 8,
    "killer": 9,
    "journalist": 9,
    "hired_killer": 12,
    "werewolf": 19,
    "prankster": 10,
}

ROLE_MIN_KEYS: dict[Role, str] = {
    Role.DON: "don",
    Role.COMMISSAR: "commissar",
    Role.DOCTOR: "doctor",
    Role.MAFIA: "mafia",
    Role.LAWYER: "advocate",
    Role.SPY: "spy",
    Role.KILLER: "killer",
    Role.WOLF: "werewolf",
    Role.JOURNALIST: "journalist",
    Role.HIRED_KILLER: "hired_killer",
    Role.PRANKSTER: "prankster",
}

SUPER_ROLE_ORDER: tuple[Role, ...] = (
    Role.CITIZEN,
    Role.CITIZEN,
    Role.DON,
    Role.COMMISSAR,
    Role.DOCTOR,
    Role.MAFIA,
    Role.MISTRESS,
    Role.LUCKY,
    Role.GUARD,
    Role.SORCERER,
    Role.LAWYER,
    Role.PRANKSTER,
    Role.MAYOR,
    Role.HOJIAKA,
    Role.MASHKA,
    Role.ARSONIST,
    Role.SPY,
    Role.WATCHER,
    Role.HIRED_KILLER,
    Role.MINER,
    Role.MAQ,
    Role.WATCHER,
    Role.DOCTOR,
    Role.CROOK,
    Role.KILLER,
    Role.BUM,
    Role.JOURNALIST,
    Role.WOLF,
    Role.SNITCH,
    Role.MAFIA,
    Role.JUDGE,
    Role.WOLF,
    Role.SERGEANT,
    Role.SORCERER,
    Role.MAFIA,
    Role.SERGEANT,
    Role.MAYOR,
    Role.WOLF,
    Role.CROOK,
    Role.MAFIA,
    Role.SORCERER,
    Role.JESTER,
)

MEGA_ROLE_ORDER: tuple[Role, ...] = (
    Role.DON,
    Role.COMMISSAR,
    Role.DOCTOR,
    Role.BUM,
    Role.MISTRESS,
    Role.MAFIA,
    Role.GUARD,
    Role.ARSONIST,
    Role.HOJIAKA,
    Role.MASHKA,
    Role.LAWYER,
    Role.PRANKSTER,
    Role.JUDGE,
    Role.SORCERER,
    Role.MAYOR,
    Role.GUARD,
    Role.HIRED_KILLER,
    Role.MINER,
    Role.MAQ,
    Role.WATCHER,
    Role.DOCTOR,
    Role.SERGEANT,
    Role.KILLER,
    Role.MAFIA,
    Role.JOURNALIST,
    Role.CROOK,
    Role.SNITCH,
    Role.MAFIA,
    Role.JUDGE,
    Role.WOLF,
    Role.SERGEANT,
    Role.SORCERER,
    Role.MAFIA,
    Role.SERGEANT,
    Role.SPY,
    Role.WOLF,
    Role.CROOK,
    Role.MAFIA,
    Role.SORCERER,
    Role.JESTER,
    Role.MAFIA,
    Role.LUCKY,
)

ACTIVE_ROLE_POOL: tuple[Role, ...] = tuple(dict.fromkeys(SUPER_ROLE_ORDER + MEGA_ROLE_ORDER))

SUPER_FILLER_ORDER: tuple[Role, ...] = (
    Role.CITIZEN,
    Role.DOCTOR,
    Role.COMMISSAR,
    Role.SERGEANT,
    Role.MAFIA,
    Role.SORCERER,
    Role.KILLER,
)

MEGA_FILLER_ORDER: tuple[Role, ...] = (
    Role.COMMISSAR,
    Role.DOCTOR,
    Role.SERGEANT,
    Role.SORCERER,
    Role.MAFIA,
    Role.KILLER,
    Role.GUARD,
)

MAFIA_ACTIVE_ROLES: tuple[Role, ...] = (
    Role.DON,
    Role.MAFIA,
    Role.LAWYER,
    Role.SPY,
    Role.JOURNALIST,
    Role.HIRED_KILLER,
)

NON_MAFIA_ACTIVE_ROLES: tuple[Role, ...] = (
    Role.COMMISSAR,
    Role.DOCTOR,
    Role.KILLER,
    Role.WOLF,
    Role.PRANKSTER,
    Role.HOJIAKA,
    Role.MASHKA,
)

REPEATABLE_ACTIVE_ROLES: tuple[Role, ...] = (
    Role.COMMISSAR,
    Role.DOCTOR,
    Role.KILLER,
    Role.HOJIAKA,
    Role.MASHKA,
)


def role_preset_label(preset: str) -> str:
    return ROLE_PRESET_LABELS.get(preset, ROLE_PRESET_LABELS["black23"])


def role_preset_max_players(preset: str) -> int:
    return ROLE_PRESET_MAX_PLAYERS.get(preset, ROLE_PRESET_MAX_PLAYERS["black23"])


def normalize_game_mode(mode: str = GAME_MODE_CLASSIC) -> str:
    if mode in {"black23", "extended35"}:
        return GAME_MODE_CLASSIC
    return mode if mode in GAME_MODES else GAME_MODE_CLASSIC


def get_available_roles(
    mode: str,
    player_count: int,
    disabled_roles: Optional[set[Role]] = None,
    *,
    include_future_thresholds: bool = False,
) -> list[Role]:
    disabled = disabled_roles or set()
    if normalize_game_mode(mode) == GAME_MODE_CLASSIC:
        return [role for role in build_role_set(player_count, GAME_MODE_CLASSIC) if role not in disabled]

    order = MEGA_ROLE_ORDER if normalize_game_mode(mode) == GAME_MODE_MEGA else SUPER_ROLE_ORDER
    max_count = len(order) if include_future_thresholds else max(4, player_count)
    return [
        role
        for role in dict.fromkeys(order[:max_count])
        if role not in disabled
    ]


def _max_mafia_slots(player_count: int) -> int:
    """Balance mafia-side roles so the game cannot auto-finish at start."""
    if player_count <= 7:
        return 1
    if player_count <= 11:
        return 2
    if player_count <= 15:
        return 3
    if player_count <= 19:
        return 4
    return max(4, min(6, player_count // 5 + 1))


def _eligible_active_roles(player_count: int, disabled_roles: set[Role]) -> list[Role]:
    return get_available_roles(GAME_MODE_SUPER, player_count, disabled_roles)


def _split_active_roles(player_count: int, disabled_roles: set[Role]) -> tuple[list[Role], list[Role]]:
    eligible = _eligible_active_roles(player_count, disabled_roles)
    max_mafia = _max_mafia_slots(player_count)
    mafia_roles = [role for role in MAFIA_ACTIVE_ROLES if role in eligible][:max_mafia]
    non_mafia_roles = [role for role in NON_MAFIA_ACTIVE_ROLES if role in eligible]
    return mafia_roles, non_mafia_roles


def _build_super_roles(player_count: int, disabled_roles: set[Role]) -> list[Role]:
    roles = [role for role in SUPER_ROLE_ORDER[:player_count] if role not in disabled_roles]
    filler = [role for role in SUPER_FILLER_ORDER if role not in disabled_roles] or [Role.CITIZEN]
    if player_count < 12:
        roles = [role for role in roles if role != Role.PRANKSTER]
    idx = 0
    while len(roles) < player_count:
        roles.append(filler[idx % len(filler)])
        idx += 1
    return roles[:player_count]


def _build_mega_roles(player_count: int, disabled_roles: set[Role]) -> list[Role]:
    roles = [role for role in MEGA_ROLE_ORDER[:player_count] if role not in disabled_roles]
    filler = [role for role in MEGA_FILLER_ORDER if role not in disabled_roles] or [Role.COMMISSAR]
    if player_count < 10:
        roles = [role for role in roles if role != Role.PRANKSTER]
    idx = 0
    while len(roles) < player_count:
        roles.append(filler[idx % len(filler)])
        idx += 1
    return roles[:player_count]


def generate_roles_for_game(
    mode: str,
    player_count: int,
    disabled_roles: Optional[set[Role]] = None,
    rng: Optional[Callable[[list[Role]], None]] = None,
) -> list[Role]:
    if player_count < 4:
        raise ValueError("Kamida 4 ta o'yinchi kerak.")

    normalized_mode = normalize_game_mode(mode)
    disabled = disabled_roles or set()
    if normalized_mode == GAME_MODE_SUPER:
        roles = _build_super_roles(player_count, disabled)
    elif normalized_mode == GAME_MODE_MEGA:
        roles = _build_mega_roles(player_count, disabled)
    else:
        roles = _build_classic_role_set(player_count, disabled)

    shuffler = rng or shuffle
    shuffler(roles)
    if len(roles) != player_count:
        raise ValueError(f"Role count mismatch: expected {player_count}, got {len(roles)}")
    return roles[:player_count]


def _build_classic_role_set(player_count: int, disabled_roles: Optional[set[Role]] = None) -> list[Role]:
    disabled = disabled_roles or set()
    capped_count = min(player_count, 30)
    if capped_count <= 30:
        roles = EXTENDED_ROLE_TABLE.get(capped_count, EXTENDED_ROLE_TABLE[4]).copy()
    else:
        roles = EXTENDED_ROLE_TABLE[30].copy()

    if player_count > len(roles):
        extras = [Role.CITIZEN, Role.CITIZEN, Role.MAFIA, Role.CITIZEN]
        idx = 0
        while len(roles) < player_count:
            roles.append(extras[idx % len(extras)])
            idx += 1

    if player_count <= 18:
        roles = [Role.CITIZEN if role == Role.WOLF else role for role in roles]

    roles = [role for role in roles if role not in disabled]
    if len(roles) < player_count:
        fallback = [Role.CITIZEN, Role.MAFIA, Role.DOCTOR, Role.COMMISSAR, Role.DON]
        idx = 0
        while len(roles) < player_count:
            candidate = fallback[idx % len(fallback)]
            if candidate not in disabled:
                roles.append(candidate)
            idx += 1
            if idx > 100 and len(roles) < player_count:
                roles.append(Role.CITIZEN)

    return roles[:player_count]


def build_role_set(
    player_count: int,
    preset: str = "black23",
    disabled_roles: Optional[set[Role]] = None,
    rng: Optional[Callable[[list[Role]], None]] = None,
) -> list[Role]:
    return generate_roles_for_game(preset, player_count, disabled_roles=disabled_roles, rng=rng)
