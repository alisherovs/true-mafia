from __future__ import annotations

from enum import Enum


class Language(str, Enum):
    AZ = "az"
    TR = "tr"
    EN = "en"
    RU = "ru"
    UA = "ua"
    KZ = "kz"
    UZ = "uz"
    ID = "id"


class GameStatus(str, Enum):
    REGISTRATION = "registration"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class GamePhase(str, Enum):
    REGISTRATION = "registration"
    NIGHT = "night"
    DAY_DISCUSSION = "day_discussion"
    DAY_VOTING = "day_voting"
    DAY_CONFIRM = "day_confirm"
    ENDED = "ended"


class Team(str, Enum):
    CITY = "city"
    MAFIA = "mafia"
    KILLER = "killer"
    NEUTRAL = "neutral"


class Role(str, Enum):
    CITIZEN = "citizen"
    MISTRESS = "mistress"
    SERGEANT = "sergeant"
    COMMISSAR = "commissar"
    DOCTOR = "doctor"
    GUARD = "guard"
    WATCHER = "watcher"
    JUDGE = "judge"
    BUM = "bum"
    SORCERER = "sorcerer"

    DON = "don"
    MAFIA = "mafia"
    LAWYER = "lawyer"
    SPY = "spy"

    KILLER = "killer"
    WOLF = "wolf"
    JESTER = "jester"


class ActionType(str, Enum):
    KILL = "kill"
    HEAL = "heal"
    CHECK = "check"
    SHOOT = "shoot"
    BLOCK = "block"
    DEFEND = "defend"
    GUARD = "guard"
    WATCH = "watch"
    VISIT = "visit"
    REVENGE_PICK = "revenge_pick"


class LogType(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    GAME_EVENT = "game_event"
