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
        "Siz tunda 10 ta kondan birini tanlaysiz. U yerda 3 ta o'limli, 2 ta 💎'li va 5 ta 💵'li kon bor. Siz tanlagan koningizdagi narsani olasiz. Oxirigacha tirik qolsangiz yutasiz.",
    ),
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


EXTENDED_ROLE_TABLE: dict[int, list[Role]] = {
    count: _with_miner(count, roles)
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
    "super": 30,
    "mega": 30,
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
    "mafia": 7,
    "advocate": 8,
    "spy": 8,
    "killer": 9,
    "journalist": 9,
}

ROLE_MIN_KEYS: dict[Role, str] = {
    Role.DON: "don",
    Role.COMMISSAR: "commissar",
    Role.DOCTOR: "doctor",
    Role.MAFIA: "mafia",
    Role.LAWYER: "advocate",
    Role.SPY: "spy",
    Role.HIRED_KILLER: "killer",
    Role.JOURNALIST: "journalist",
}

ACTIVE_ROLE_POOL: tuple[Role, ...] = (
    Role.DON,
    Role.COMMISSAR,
    Role.DOCTOR,
    Role.MAFIA,
    Role.LAWYER,
    Role.SPY,
    Role.HIRED_KILLER,
    Role.JOURNALIST,
)

REPEATABLE_ACTIVE_ROLES: tuple[Role, ...] = (
    Role.MAFIA,
    Role.DOCTOR,
    Role.SPY,
    Role.HIRED_KILLER,
    Role.JOURNALIST,
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

    max_threshold = max(ROLE_MIN_PLAYERS.values()) if include_future_thresholds else player_count
    return [
        role
        for role in ACTIVE_ROLE_POOL
        if ROLE_MIN_PLAYERS[ROLE_MIN_KEYS[role]] <= max_threshold and role not in disabled
    ]


def _build_super_roles(player_count: int, disabled_roles: set[Role]) -> list[Role]:
    roles = get_available_roles(GAME_MODE_SUPER, player_count, disabled_roles)
    if len(roles) < player_count:
        roles.extend([Role.CITIZEN] * (player_count - len(roles)))
    return roles[:player_count]


def _build_mega_roles(player_count: int, disabled_roles: set[Role]) -> list[Role]:
    roles = get_available_roles(GAME_MODE_MEGA, player_count, disabled_roles)
    if len(roles) < player_count:
        for role in get_available_roles(
            GAME_MODE_MEGA,
            player_count,
            disabled_roles,
            include_future_thresholds=True,
        ):
            if role not in roles:
                roles.append(role)
            if len(roles) >= player_count:
                break

    repeatable = [role for role in REPEATABLE_ACTIVE_ROLES if role not in disabled_roles]
    idx = 0
    while len(roles) < player_count and repeatable:
        roles.append(repeatable[idx % len(repeatable)])
        idx += 1

    if len(roles) < player_count:
        safe_base = [role for role in ACTIVE_ROLE_POOL if role not in disabled_roles] or list(ACTIVE_ROLE_POOL)
        idx = 0
        while len(roles) < player_count:
            roles.append(safe_base[idx % len(safe_base)])
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
