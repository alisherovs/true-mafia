from __future__ import annotations

from typing import Optional
from app.texts import az, en, id, kz, ru, tr, ua, uz

ALL_TEXTS = {
    "uz": uz.TEXTS,
    "ru": ru.TEXTS,
    "en": en.TEXTS,
    "az": az.TEXTS,
    "tr": tr.TEXTS,
    "ua": ua.TEXTS,
    "kz": kz.TEXTS,
    "id": id.TEXTS,
}


def t(lang: Optional[str], key: str, **kwargs: object) -> str:
    language = (lang or "uz").lower()
    value = ALL_TEXTS.get(language, ALL_TEXTS["uz"]).get(key)
    if value is None:
        value = ALL_TEXTS["uz"].get(key, key)
    try:
        return value.format(**kwargs)
    except Exception:
        return value
