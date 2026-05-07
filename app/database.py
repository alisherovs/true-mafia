from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, future=True, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_lightweight_columns(conn)


async def _ensure_lightweight_columns(conn) -> None:
    def missing_columns(sync_conn) -> set[str]:
        inspector = inspect(sync_conn)
        return {
            column
            for column in {"inactive_rounds", "last_words", "awaiting_last_words", "judge_cancel_used"}
            if column not in {item["name"] for item in inspector.get_columns("game_players")}
        }

    missing = await conn.run_sync(missing_columns)
    if "inactive_rounds" in missing:
        await conn.execute(text("ALTER TABLE game_players ADD COLUMN inactive_rounds INTEGER DEFAULT 0"))
    if "last_words" in missing:
        await conn.execute(text("ALTER TABLE game_players ADD COLUMN last_words TEXT"))
    if "awaiting_last_words" in missing:
        await conn.execute(text("ALTER TABLE game_players ADD COLUMN awaiting_last_words BOOLEAN DEFAULT 0"))
    if "judge_cancel_used" in missing:
        await conn.execute(text("ALTER TABLE game_players ADD COLUMN judge_cancel_used BOOLEAN DEFAULT 0"))

    def game_missing_columns(sync_conn) -> set[str]:
        inspector = inspect(sync_conn)
        return {
            column
            for column in {"active_key"}
            if column not in {item["name"] for item in inspector.get_columns("games")}
        }

    game_missing = await conn.run_sync(game_missing_columns)
    if "active_key" in game_missing:
        await conn.execute(text("ALTER TABLE games ADD COLUMN active_key INTEGER"))
    await conn.execute(
        text(
            "UPDATE games SET status = 'cancelled', phase = 'ended', active_key = NULL "
            "WHERE status IN ('registration', 'active') "
            "AND id NOT IN ("
            "SELECT latest_id FROM ("
            "SELECT MAX(id) AS latest_id FROM games WHERE status IN ('registration', 'active') GROUP BY chat_id"
            ")"
            ")"
        )
    )
    await conn.execute(text("UPDATE games SET active_key = 1 WHERE status IN ('registration', 'active')"))
    await conn.execute(text("UPDATE games SET active_key = NULL WHERE status NOT IN ('registration', 'active')"))
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_game_per_chat ON games(chat_id, active_key)"))
