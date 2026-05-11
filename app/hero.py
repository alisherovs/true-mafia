from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import Optional

from app.enums import Role


HERO_BUY_PRICE_DIAMONDS = 100
HERO_ADD_POINTS_PRICE_DIAMONDS = 50
HERO_ADD_POINTS_AMOUNT = 1000
HERO_UPGRADE_DEFENSE_PRICE_DOLLAR = 1200
HERO_RECHARGE_PRICE_DOLLAR = 1300
HERO_RENAME_PRICE_DOLLAR = 2500
HERO_CANCEL_SALE_PRICE_DIAMONDS = 1
HERO_DEFAULT_NAME = "Master"
HERO_DEFAULT_CHARGE = 10
HERO_MAX_CHARGE = 10
HERO_DEFAULT_HP = 100
HERO_ALLOWED_ROLES = {Role.DON, Role.MAFIA, Role.KILLER, Role.HIRED_KILLER, Role.GUARD, Role.COMMISSAR}
HERO_MARKET_CHANNEL_KEY = "hero_market_channel_id"


HERO_LEVELS: dict[int, dict[str, Optional[float | int | bool]]] = {
    1: {"points": 0, "power_min": 40, "power_max": 47, "max_defense": 0.0, "max_hit": False},
    2: {"points": 1100, "power_min": 47, "power_max": 54, "max_defense": 0.0, "max_hit": False},
    3: {"points": 2200, "power_min": 54, "power_max": 61, "max_defense": 10.0, "max_hit": False},
    4: {"points": 3300, "power_min": 61, "power_max": 68, "max_defense": 20.0, "max_hit": False},
    5: {"points": 4400, "power_min": 68, "power_max": 75, "max_defense": 30.0, "max_hit": False},
    6: {"points": 5500, "power_min": 75, "power_max": 82, "max_defense": 40.0, "max_hit": False},
    7: {"points": 6600, "power_min": 82, "power_max": 89, "max_defense": 50.0, "max_hit": False},
    8: {"points": 7700, "power_min": 89, "power_max": 96, "max_defense": 50.0, "max_hit": False},
    9: {"points": 8800, "power_min": 96, "power_max": 103, "max_defense": 60.0, "max_hit": False},
    10: {"points": 9900, "power_min": None, "power_max": None, "max_defense": 70.0, "max_hit": True},
    11: {"points": 11000, "power_min": None, "power_max": None, "max_defense": 80.0, "max_hit": True},
    12: {"points": 12100, "power_min": None, "power_max": None, "max_defense": 90.0, "max_hit": True},
}


@dataclass(frozen=True)
class HeroLevelInfo:
    level: int
    next_level: Optional[int]
    next_points: Optional[int]
    power_text: str
    max_defense: float
    max_hit: bool


def hero_level_for_points(points: int) -> HeroLevelInfo:
    level = 1
    for candidate, data in HERO_LEVELS.items():
        if points >= int(data["points"] or 0):
            level = candidate
    data = HERO_LEVELS[level]
    next_level = level + 1 if level < max(HERO_LEVELS) else None
    next_points = int(HERO_LEVELS[next_level]["points"]) if next_level else None
    if data["max_hit"]:
        power_text = "maksimal uradi"
    else:
        power_text = f"{int(data['power_min'])}-{int(data['power_max'])}"
    return HeroLevelInfo(
        level=level,
        next_level=next_level,
        next_points=next_points,
        power_text=power_text,
        max_defense=float(data["max_defense"] or 0.0),
        max_hit=bool(data["max_hit"]),
    )


def sanitize_hero_name(raw: str) -> tuple[bool, str]:
    name = " ".join((raw or "").strip().split())
    if not 2 <= len(name) <= 20:
        return False, "❌ Geroy nomi 2-20 belgi orasida bo'lishi kerak."
    lowered = name.lower()
    if (
        "http://" in lowered
        or "https://" in lowered
        or "t.me/" in lowered
        or "@" in name
        or name.startswith("/")
        or re.search(r"[<>`]", name)
    ):
        return False, "❌ Link, @username, command yoki xavfli belgilar qabul qilinmaydi."
    return True, name


def safe_hero_name(name: str) -> str:
    return escape(name or HERO_DEFAULT_NAME)
