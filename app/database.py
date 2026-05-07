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
    async def table_columns(table_name: str) -> set[str]:
        def read_columns(sync_conn) -> set[str]:
            inspector = inspect(sync_conn)
            if not inspector.has_table(table_name):
                return set()
            return {item["name"] for item in inspector.get_columns(table_name)}

        return await conn.run_sync(read_columns)

    async def add_missing_columns(table_name: str, columns: dict[str, str]) -> None:
        existing = await table_columns(table_name)
        for column, definition in columns.items():
            if column not in existing:
                await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column} {definition}"))

    await add_missing_columns(
        "users",
        {
            "username": "VARCHAR(255)",
            "display_name": "VARCHAR(255) DEFAULT 'Unknown'",
            "language": "VARCHAR(8) DEFAULT 'uz'",
            "language_selected": "BOOLEAN DEFAULT FALSE",
            "dollar": "INTEGER DEFAULT 0",
            "diamonds": "INTEGER DEFAULT 0",
            "protection": "INTEGER DEFAULT 0",
            "killer_protection": "INTEGER DEFAULT 0",
            "vote_protection": "INTEGER DEFAULT 0",
            "gun": "INTEGER DEFAULT 0",
            "mask": "INTEGER DEFAULT 0",
            "fake_document": "INTEGER DEFAULT 0",
            "next_game_role": "VARCHAR(64)",
            "wins": "INTEGER DEFAULT 0",
            "total_games": "INTEGER DEFAULT 0",
        },
    )
    await add_missing_columns(
        "groups",
        {
            "registration_timeout": "INTEGER DEFAULT 90",
            "min_players": "INTEGER DEFAULT 4",
            "role_preset": "VARCHAR(32) DEFAULT 'black23'",
            "premium_until": "DATETIME",
        },
    )
    await add_missing_columns(
        "games",
        {
            "active_key": "INTEGER",
            "day_number": "INTEGER DEFAULT 0",
            "night_number": "INTEGER DEFAULT 0",
            "lobby_message_id": "BIGINT",
            "registration_ends_at": "DATETIME",
            "started_at": "DATETIME",
            "ended_at": "DATETIME",
            "winner_team": "VARCHAR(32)",
        },
    )
    await add_missing_columns(
        "game_players",
        {
            "user_id": "INTEGER",
            "role": "VARCHAR(64)",
            "team": "VARCHAR(32)",
            "alive": "BOOLEAN DEFAULT TRUE",
            "self_heal_used": "BOOLEAN DEFAULT FALSE",
            "judge_cancel_used": "BOOLEAN DEFAULT FALSE",
            "inactive_rounds": "INTEGER DEFAULT 0",
            "last_words": "TEXT",
            "awaiting_last_words": "BOOLEAN DEFAULT FALSE",
            "death_day": "INTEGER",
            "won": "BOOLEAN DEFAULT FALSE",
            "transformed_to_role": "VARCHAR(64)",
            "transformed_to_team": "VARCHAR(32)",
        },
    )

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
        await conn.execute(text("ALTER TABLE game_players ADD COLUMN awaiting_last_words BOOLEAN DEFAULT FALSE"))
    if "judge_cancel_used" in missing:
        await conn.execute(text("ALTER TABLE game_players ADD COLUMN judge_cancel_used BOOLEAN DEFAULT FALSE"))

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

    await conn.execute(
        text(
            "DELETE FROM night_actions "
            "WHERE id NOT IN ("
            "SELECT keep_id FROM ("
            "SELECT MIN(id) AS keep_id FROM night_actions GROUP BY game_id, night_number, actor_telegram_id"
            ")"
            ")"
        )
    )
    await conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_night_actor_once "
            "ON night_actions(game_id, night_number, actor_telegram_id)"
        )
    )
