from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import GamePhase, GameStatus


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    language: Mapped[str] = mapped_column(String(8), default="uz")
    language_selected: Mapped[bool] = mapped_column(Boolean, default=False)

    dollar: Mapped[int] = mapped_column(Integer, default=0)
    diamonds: Mapped[int] = mapped_column(Integer, default=0)
    protection: Mapped[int] = mapped_column(Integer, default=0)
    killer_protection: Mapped[int] = mapped_column(Integer, default=0)
    vote_protection: Mapped[int] = mapped_column(Integer, default=0)
    gun: Mapped[int] = mapped_column(Integer, default=0)
    mask: Mapped[int] = mapped_column(Integer, default=0)
    fake_document: Mapped[int] = mapped_column(Integer, default=0)
    next_game_role: Mapped[str | None] = mapped_column(String(64), nullable=True)

    wins: Mapped[int] = mapped_column(Integer, default=0)
    total_games: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="Group")
    language: Mapped[str] = mapped_column(String(8), default="uz")

    registration_timeout: Mapped[int] = mapped_column(Integer, default=90)
    min_players: Mapped[int] = mapped_column(Integer, default=4)
    premium_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (UniqueConstraint("chat_id", "active_key", name="uq_active_game_per_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    creator_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)

    status: Mapped[str] = mapped_column(String(32), default=GameStatus.REGISTRATION.value)
    phase: Mapped[str] = mapped_column(String(32), default=GamePhase.REGISTRATION.value)
    active_key: Mapped[int | None] = mapped_column(Integer, nullable=True)

    day_number: Mapped[int] = mapped_column(Integer, default=0)
    night_number: Mapped[int] = mapped_column(Integer, default=0)

    lobby_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    registration_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    winner_team: Mapped[str | None] = mapped_column(String(32), nullable=True)

    players: Mapped[list[GamePlayer]] = relationship(back_populates="game", cascade="all, delete-orphan")


class GamePlayer(Base):
    __tablename__ = "game_players"
    __table_args__ = (UniqueConstraint("game_id", "telegram_id", name="uq_game_player"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)

    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    display_name: Mapped[str] = mapped_column(String(255))

    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    team: Mapped[str | None] = mapped_column(String(32), nullable=True)

    alive: Mapped[bool] = mapped_column(Boolean, default=True)
    self_heal_used: Mapped[bool] = mapped_column(Boolean, default=False)
    judge_cancel_used: Mapped[bool] = mapped_column(Boolean, default=False)
    inactive_rounds: Mapped[int] = mapped_column(Integer, default=0)
    last_words: Mapped[str | None] = mapped_column(Text, nullable=True)
    awaiting_last_words: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    death_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    won: Mapped[bool] = mapped_column(Boolean, default=False)

    transformed_to_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transformed_to_team: Mapped[str | None] = mapped_column(String(32), nullable=True)

    game: Mapped[Game] = relationship(back_populates="players")


class NightAction(Base):
    __tablename__ = "night_actions"
    __table_args__ = (UniqueConstraint("game_id", "night_number", "actor_telegram_id", "action_type", name="uq_night_action"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)

    night_number: Mapped[int] = mapped_column(Integer, index=True)
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    action_type: Mapped[str] = mapped_column(String(32))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("game_id", "day_number", "voter_telegram_id", name="uq_vote"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    day_number: Mapped[int] = mapped_column(Integer, index=True)

    voter_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HangVote(Base):
    __tablename__ = "hang_votes"
    __table_args__ = (UniqueConstraint("game_id", "day_number", "voter_telegram_id", name="uq_hang_vote"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    day_number: Mapped[int] = mapped_column(Integer, index=True)
    target_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    voter_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    approve: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PremiumRecord(Base):
    __tablename__ = "premium_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    group_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    amount: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(16), default="USD")
    days_added: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(64), default="manual")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PremiumGroup(Base):
    __tablename__ = "premium_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    invite_link: Mapped[str] = mapped_column(Text)
    diamond_price: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GameLog(Base):
    __tablename__ = "game_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
