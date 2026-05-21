from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import suppress

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine_options = {
    "future": True,
    "echo": False,
    "pool_pre_ping": True,
}
if not settings.database_url.startswith("sqlite"):
    engine_options.update(
        {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_timeout": settings.db_pool_timeout,
        }
    )
engine = create_async_engine(settings.database_url, **engine_options)
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
            "miner_protection": "INTEGER DEFAULT 0",
            "drug_protection": "INTEGER DEFAULT 0",
            "mask": "INTEGER DEFAULT 0",
            "fake_document": "INTEGER DEFAULT 0",
            "next_game_role": "VARCHAR(64)",
            "next_game_disabled_role": "VARCHAR(64)",
            "use_protection": "BOOLEAN DEFAULT TRUE",
            "use_killer_protection": "BOOLEAN DEFAULT TRUE",
            "use_vote_protection": "BOOLEAN DEFAULT TRUE",
            "use_miner_protection": "BOOLEAN DEFAULT TRUE",
            "use_drug_protection": "BOOLEAN DEFAULT TRUE",
            "use_mask": "BOOLEAN DEFAULT TRUE",
            "use_fake_document": "BOOLEAN DEFAULT TRUE",
            "wins": "INTEGER DEFAULT 0",
            "total_games": "INTEGER DEFAULT 0",
            "play_locked_until": "DATETIME",
            "vip_until": "DATETIME",
        },
    )
    await add_missing_columns(
        "groups",
        {
            "registration_timeout": "INTEGER DEFAULT 90",
            "night_timeout": "INTEGER DEFAULT 60",
            "day_discussion_timeout": "INTEGER DEFAULT 45",
            "day_voting_timeout": "INTEGER DEFAULT 60",
            "min_players": "INTEGER DEFAULT 4",
            "role_preset": "VARCHAR(32) DEFAULT 'black23'",
            "welcome_enabled": "BOOLEAN DEFAULT TRUE",
            "welcome_text": "TEXT DEFAULT 'guruhga xush kelibsiz!'",
            "welcome_media_type": "VARCHAR(16) DEFAULT ''",
            "welcome_media_file_id": "TEXT DEFAULT ''",
            "premium_until": "DATETIME",
        },
    )
    await add_missing_columns(
        "premium_groups",
        {
            "total_diamonds": "INTEGER DEFAULT 0",
            "group_chat_id": "BIGINT",
            "top_sender_telegram_id": "BIGINT",
            "top_sender_name": "VARCHAR(255)",
            "top_sender_diamonds": "INTEGER DEFAULT 0",
            "reset_at": "DATETIME",
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
        "group_settings",
        {
            "giveaway_diamond": "INTEGER DEFAULT 0",
            "giveaway_protection": "INTEGER DEFAULT 0",
            "leave_allowed": "BOOLEAN DEFAULT TRUE",
            "leave_lock_minutes": "INTEGER DEFAULT 30",
            "game_mode": "VARCHAR(32) DEFAULT 'normal'",
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
            "blocked_until_day": "INTEGER DEFAULT 0",
            "inactive_rounds": "INTEGER DEFAULT 0",
            "last_words": "TEXT",
            "awaiting_last_words": "BOOLEAN DEFAULT FALSE",
            "death_day": "INTEGER",
            "won": "BOOLEAN DEFAULT FALSE",
            "transformed_to_role": "VARCHAR(64)",
            "transformed_to_team": "VARCHAR(32)",
            "hero_hp": "INTEGER DEFAULT 100",
            "hero_max_hp": "INTEGER DEFAULT 100",
            "hero_defense_active": "BOOLEAN DEFAULT FALSE",
            "hero_defense_amount": "INTEGER DEFAULT 0",
            "killed_by_hero": "BOOLEAN DEFAULT FALSE",
            "sorcerer_revenge_used": "BOOLEAN DEFAULT FALSE",
            "left_game": "BOOLEAN DEFAULT FALSE",
        },
    )
    await add_missing_columns(
        "gamble_mines_games",
        {
            "opponent_telegram_id": "BIGINT",
            "winner_telegram_id": "BIGINT",
            "game_kind": "VARCHAR(16) DEFAULT 'duel'",
        },
    )
    await add_missing_columns(
        "heroes",
        {
            "is_active": "BOOLEAN DEFAULT FALSE",
        },
    )
    await _drop_sqlite_hero_owner_unique(conn)

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
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_games_chat_status_id ON games(chat_id, status, id)"))
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_games_status_registration_ends ON games(status, registration_ends_at)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_game_players_game_alive_role ON game_players(game_id, alive, role)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_game_players_telegram_alive ON game_players(telegram_id, alive)")
    )
    with suppress(Exception):
        await conn.execute(text("DROP INDEX IF EXISTS uq_hero_owner"))
    with suppress(Exception):
        await conn.execute(text("ALTER TABLE heroes DROP CONSTRAINT uq_hero_owner"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_heroes_owner_user_id ON heroes(owner_user_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_heroes_owner_active ON heroes(owner_user_id, is_active)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_heroes_for_sale ON heroes(is_for_sale, sale_price_diamonds)"))
    await conn.execute(
        text(
            "UPDATE heroes SET is_active = TRUE WHERE id IN ("
            "SELECT latest_id FROM ("
            "SELECT MAX(id) AS latest_id FROM heroes GROUP BY owner_user_id "
            "HAVING SUM(CASE WHEN is_active THEN 1 ELSE 0 END) = 0"
            ")"
            ")"
        )
    )

    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_premium_groups_total_diamonds ON premium_groups(total_diamonds)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_premium_groups_group_chat_id ON premium_groups(group_chat_id)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_activity_score_chat_created ON activity_score_events(chat_id, created_at)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_activity_score_user_created ON activity_score_events(user_telegram_id, created_at)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_gamble_mines_opponent_status ON gamble_mines_games(opponent_telegram_id, status)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_gamble_mines_winner_created ON gamble_mines_games(winner_telegram_id, ended_at)")
    )
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_couple_chat_active ON couple_relationships(chat_id, active)"))
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_couple_user_one_active ON couple_relationships(user_one_telegram_id, active)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_couple_user_two_active ON couple_relationships(user_two_telegram_id, active)")
    )
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_premium_groups_reset_at ON premium_groups(reset_at)"))
    await conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_premium_group_contributor "
            "ON premium_group_contributions(premium_group_id, user_telegram_id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_premium_group_contributions_group_amount "
            "ON premium_group_contributions(premium_group_id, diamonds)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_premium_blocked_users_created_at "
            "ON premium_blocked_users(created_at)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_diamond_transactions_created_at "
            "ON diamond_transactions(created_at)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_diamond_transactions_user_created "
            "ON diamond_transactions(user_telegram_id, created_at)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_diamond_transactions_action "
            "ON diamond_transactions(action)"
        )
    )

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

    # Migrate old group_settings table if it has 'id' column (old schema)
    gs_cols = await table_columns("group_settings")
    if gs_cols and "id" in gs_cols:
        await conn.execute(text("DROP TABLE IF EXISTS group_settings"))
        await conn.execute(text("DROP TABLE IF EXISTS group_role_settings"))
        await conn.execute(text("DROP TABLE IF EXISTS group_weapon_settings"))
        await conn.execute(text("DROP TABLE IF EXISTS group_command_permissions"))
        await conn.execute(text("DROP TABLE IF EXISTS group_chat_permissions"))
        await conn.execute(text("DROP TABLE IF EXISTS group_time_settings"))
        await conn.execute(text("DROP TABLE IF EXISTS group_extra_settings"))
        from app.database import Base as _Base
        await conn.run_sync(_Base.metadata.create_all)


async def _drop_sqlite_hero_owner_unique(conn) -> None:
    if conn.dialect.name != "sqlite":
        return
    row = (
        await conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='heroes'")
        )
    ).first()
    sql = row[0] if row else ""
    if "uq_hero_owner" not in (sql or ""):
        return
    await conn.execute(text("PRAGMA foreign_keys=OFF"))
    await conn.execute(
        text(
            "CREATE TABLE heroes_new ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "owner_user_id INTEGER NOT NULL, "
            "name VARCHAR(20) NOT NULL, "
            "points INTEGER NOT NULL, "
            "level INTEGER NOT NULL, "
            "current_defense INTEGER NOT NULL, "
            "max_defense FLOAT NOT NULL, "
            "charge INTEGER NOT NULL, "
            "max_charge INTEGER NOT NULL, "
            "is_active BOOLEAN DEFAULT FALSE, "
            "is_for_sale BOOLEAN NOT NULL, "
            "sale_price_diamonds INTEGER, "
            "sale_channel_message_id BIGINT, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, "
            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, "
            "FOREIGN KEY(owner_user_id) REFERENCES users (id)"
            ")"
        )
    )
    await conn.execute(
        text(
            "INSERT INTO heroes_new ("
            "id, owner_user_id, name, points, level, current_defense, max_defense, "
            "charge, max_charge, is_active, is_for_sale, sale_price_diamonds, "
            "sale_channel_message_id, created_at, updated_at"
            ") "
            "SELECT id, owner_user_id, name, points, level, current_defense, max_defense, "
            "charge, max_charge, COALESCE(is_active, FALSE), is_for_sale, sale_price_diamonds, "
            "sale_channel_message_id, created_at, updated_at FROM heroes"
        )
    )
    await conn.execute(text("DROP TABLE heroes"))
    await conn.execute(text("ALTER TABLE heroes_new RENAME TO heroes"))
    await conn.execute(text("PRAGMA foreign_keys=ON"))
