from __future__ import annotations

from typing import Optional
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    bot_username: str = Field(alias="BOT_USERNAME")
    database_url: str = Field(default="sqlite+aiosqlite:///./storage/mafia.db", alias="DATABASE_URL")
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=30, alias="DB_POOL_TIMEOUT")

    default_language: str = Field(default="uz", alias="DEFAULT_LANGUAGE")
    news_channel_url: str = Field(default="https://t.me/WorldMafiaNews", alias="NEWS_CHANNEL_URL")
    news_bonus_channel: str = Field(default="@WorldMafiaNews", alias="NEWS_BONUS_CHANNEL")
    support_url: str = Field(default="https://t.me", alias="SUPPORT_URL")
    admin_username: str = Field(default="support", alias="ADMIN_USERNAME")
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")

    min_players: int = Field(default=4, alias="MIN_PLAYERS")
    registration_timeout: int = Field(default=90, alias="REGISTRATION_TIMEOUT")
    night_timeout: int = Field(default=60, alias="NIGHT_TIMEOUT")
    day_discussion_timeout: int = Field(default=45, alias="DAY_DISCUSSION_TIMEOUT")
    day_voting_timeout: int = Field(default=60, alias="DAY_VOTING_TIMEOUT")

    winner_reward_dollar: int = Field(default=15, alias="WINNER_REWARD_DOLLAR")
    winner_reward_diamond: int = Field(default=0, alias="WINNER_REWARD_DIAMOND")
    loser_reward_dollar: int = Field(default=5, alias="LOSER_REWARD_DOLLAR")
    loser_reward_diamond: int = Field(default=0, alias="LOSER_REWARD_DIAMOND")

    night_media_file_id: Optional[str] = Field(default=None, alias="NIGHT_MEDIA_FILE_ID")
    day_media_file_id: Optional[str] = Field(default=None, alias="DAY_MEDIA_FILE_ID")
    night_media_local: str = Field(default="media/night.gif", alias="NIGHT_MEDIA_LOCAL")
    day_media_local: str = Field(default="media/day.gif", alias="DAY_MEDIA_LOCAL")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def admin_ids(self) -> set[int]:
        ids: set[int] = set()
        for raw in self.admin_ids_raw.replace(";", ",").split(","):
            item = raw.strip()
            if item.lstrip("-").isdigit():
                ids.add(int(item))
        return ids


@lru_cache
def get_settings() -> Settings:
    return Settings()
