from __future__ import annotations

from typing import Optional
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import GamePhase, GameStatus


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    language: Mapped[str] = mapped_column(String(8), default="uz")
    language_selected: Mapped[bool] = mapped_column(Boolean, default=False)

    dollar: Mapped[int] = mapped_column(Integer, default=0)
    diamonds: Mapped[int] = mapped_column(Integer, default=0)
    protection: Mapped[int] = mapped_column(Integer, default=0)
    killer_protection: Mapped[int] = mapped_column(Integer, default=0)
    vote_protection: Mapped[int] = mapped_column(Integer, default=0)
    miner_protection: Mapped[int] = mapped_column(Integer, default=0)
    drug_protection: Mapped[int] = mapped_column(Integer, default=0)
    mask: Mapped[int] = mapped_column(Integer, default=0)
    fake_document: Mapped[int] = mapped_column(Integer, default=0)
    next_game_role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    next_game_disabled_role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    use_protection: Mapped[bool] = mapped_column(Boolean, default=True)
    use_killer_protection: Mapped[bool] = mapped_column(Boolean, default=True)
    use_vote_protection: Mapped[bool] = mapped_column(Boolean, default=True)
    use_miner_protection: Mapped[bool] = mapped_column(Boolean, default=True)
    use_drug_protection: Mapped[bool] = mapped_column(Boolean, default=True)
    use_mask: Mapped[bool] = mapped_column(Boolean, default=True)
    use_fake_document: Mapped[bool] = mapped_column(Boolean, default=True)

    wins: Mapped[int] = mapped_column(Integer, default=0)
    total_games: Mapped[int] = mapped_column(Integer, default=0)
    play_locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    vip_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="Group")
    language: Mapped[str] = mapped_column(String(8), default="uz")

    registration_timeout: Mapped[int] = mapped_column(Integer, default=90)
    night_timeout: Mapped[int] = mapped_column(Integer, default=60)
    day_discussion_timeout: Mapped[int] = mapped_column(Integer, default=45)
    day_voting_timeout: Mapped[int] = mapped_column(Integer, default=60)
    min_players: Mapped[int] = mapped_column(Integer, default=4)
    role_preset: Mapped[str] = mapped_column(String(32), default="black23")
    welcome_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    welcome_text: Mapped[str] = mapped_column(Text, default="guruhga xush kelibsiz!")
    welcome_media_type: Mapped[str] = mapped_column(String(16), default="")
    welcome_media_file_id: Mapped[str] = mapped_column(Text, default="")
    premium_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        UniqueConstraint("chat_id", "active_key", name="uq_active_game_per_chat"),
        Index("ix_games_chat_status_id", "chat_id", "status", "id"),
        Index("ix_games_status_registration_ends", "status", "registration_ends_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    creator_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)

    status: Mapped[str] = mapped_column(String(32), default=GameStatus.REGISTRATION.value)
    phase: Mapped[str] = mapped_column(String(32), default=GamePhase.REGISTRATION.value)
    active_key: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    role_preset: Mapped[str] = mapped_column(String(32), default="black23")

    day_number: Mapped[int] = mapped_column(Integer, default=0)
    night_number: Mapped[int] = mapped_column(Integer, default=0)

    lobby_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    registration_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    winner_team: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    players: Mapped[list[GamePlayer]] = relationship(back_populates="game", cascade="all, delete-orphan")


class GamePlayer(Base):
    __tablename__ = "game_players"
    __table_args__ = (
        UniqueConstraint("game_id", "telegram_id", name="uq_game_player"),
        Index("ix_game_players_game_alive_role", "game_id", "alive", "role"),
        Index("ix_game_players_telegram_alive", "telegram_id", "alive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)

    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    display_name: Mapped[str] = mapped_column(String(255))

    role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    team: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    alive: Mapped[bool] = mapped_column(Boolean, default=True)
    self_heal_used: Mapped[bool] = mapped_column(Boolean, default=False)
    judge_cancel_used: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_until_day: Mapped[int] = mapped_column(Integer, default=0)
    inactive_rounds: Mapped[int] = mapped_column(Integer, default=0)
    last_words: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    awaiting_last_words: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    death_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    won: Mapped[bool] = mapped_column(Boolean, default=False)

    transformed_to_role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    transformed_to_team: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    hero_hp: Mapped[int] = mapped_column(Integer, default=100)
    hero_max_hp: Mapped[int] = mapped_column(Integer, default=100)
    hero_defense_active: Mapped[bool] = mapped_column(Boolean, default=False)
    hero_defense_amount: Mapped[int] = mapped_column(Integer, default=0)
    killed_by_hero: Mapped[bool] = mapped_column(Boolean, default=False)
    sorcerer_revenge_used: Mapped[bool] = mapped_column(Boolean, default=False)
    left_game: Mapped[bool] = mapped_column(Boolean, default=False)

    game: Mapped[Game] = relationship(back_populates="players")


class Hero(Base):
    __tablename__ = "heroes"
    __table_args__ = (
        Index("ix_heroes_for_sale", "is_for_sale", "sale_price_diamonds"),
        Index("ix_heroes_owner_active", "owner_user_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(20), default="Master")
    points: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    current_defense: Mapped[int] = mapped_column(Integer, default=0)
    max_defense: Mapped[float] = mapped_column(Float, default=0.0)
    charge: Mapped[int] = mapped_column(Integer, default=10)
    max_charge: Mapped[int] = mapped_column(Integer, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_for_sale: Mapped[bool] = mapped_column(Boolean, default=False)
    sale_price_diamonds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sale_channel_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class NightAction(Base):
    __tablename__ = "night_actions"
    __table_args__ = (
        UniqueConstraint("game_id", "night_number", "actor_telegram_id", "action_type", name="uq_night_action"),
        UniqueConstraint("game_id", "night_number", "actor_telegram_id", name="uq_night_actor_once"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)

    night_number: Mapped[int] = mapped_column(Integer, index=True)
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    action_type: Mapped[str] = mapped_column(String(32))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NightPrompt(Base):
    __tablename__ = "night_prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    night_number: Mapped[int] = mapped_column(Integer, index=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    cleared: Mapped[bool] = mapped_column(Boolean, default=False)

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


class SkipDecision(Base):
    __tablename__ = "skip_decisions"
    __table_args__ = (
        UniqueConstraint("game_id", "phase", "day_number", "night_number", "user_telegram_id", name="uq_skip_decision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    phase: Mapped[str] = mapped_column(String(32), index=True)
    day_number: Mapped[int] = mapped_column(Integer, default=0, index=True)
    night_number: Mapped[int] = mapped_column(Integer, default=0, index=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PremiumRecord(Base):
    __tablename__ = "premium_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    group_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)

    amount: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(16), default="USD")
    days_added: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(64), default="manual")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PremiumGroup(Base):
    __tablename__ = "premium_groups"
    __table_args__ = (
        Index("ix_premium_groups_total_diamonds", "total_diamonds"),
        Index("ix_premium_groups_group_chat_id", "group_chat_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    invite_link: Mapped[str] = mapped_column(Text)
    diamond_price: Mapped[int] = mapped_column(Integer, default=0)
    total_diamonds: Mapped[int] = mapped_column(Integer, default=0)
    group_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    top_sender_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    top_sender_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    top_sender_diamonds: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    reset_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PremiumGroupContribution(Base):
    __tablename__ = "premium_group_contributions"
    __table_args__ = (
        UniqueConstraint("premium_group_id", "user_telegram_id", name="uq_premium_group_contributor"),
        Index("ix_premium_group_contributions_group_amount", "premium_group_id", "diamonds"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    premium_group_id: Mapped[int] = mapped_column(ForeignKey("premium_groups.id", ondelete="CASCADE"), index=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_name: Mapped[str] = mapped_column(String(255))
    diamonds: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PremiumBlockedUser(Base):
    __tablename__ = "premium_blocked_users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), default="User")
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    blocked_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DiamondGiveaway(Base):
    __tablename__ = "diamond_giveaways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    creator_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    participants_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="active")
    winner_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class DiamondTransaction(Base):
    __tablename__ = "diamond_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger)
    user_name: Mapped[str] = mapped_column(String(255), default="User")
    amount: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    counterparty_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    counterparty_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DollarTransaction(Base):
    __tablename__ = "dollar_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger)
    user_name: Mapped[str] = mapped_column(String(255), default="User")
    amount: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    counterparty_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    counterparty_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GambleMinesGame(Base):
    __tablename__ = "gamble_mines_games"
    __table_args__ = (
        Index("ix_gamble_mines_user_status", "user_telegram_id", "status"),
        Index("ix_gamble_mines_user_created", "user_telegram_id", "created_at"),
        Index("ix_gamble_mines_opponent_status", "opponent_telegram_id", "status"),
        Index("ix_gamble_mines_winner_created", "winner_telegram_id", "ended_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    opponent_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    winner_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    bet: Mapped[int] = mapped_column(Integer)
    mine_count: Mapped[int] = mapped_column(Integer, default=5)
    mines_json: Mapped[str] = mapped_column(Text, default="[]")
    opened_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(16), default="active")
    game_kind: Mapped[str] = mapped_column(String(16), default="duel")
    multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    payout: Mapped[int] = mapped_column(Integer, default=0)
    token: Mapped[str] = mapped_column(String(32), index=True)
    last_action_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class GambleUserStats(Base):
    __tablename__ = "gamble_user_stats"

    user_telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    win_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_bet: Mapped[int] = mapped_column(Integer, default=0)
    total_payout: Mapped[int] = mapped_column(Integer, default=0)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FrogGameSession(Base):
    __tablename__ = "frog_game_sessions"
    __table_args__ = (
        Index("ix_frog_sessions_user_status", "user_id", "status"),
        Index("ix_frog_sessions_chat_message", "chat_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    bet_amount: Mapped[int] = mapped_column(Integer)
    current_row: Mapped[int] = mapped_column(Integer, default=0)
    current_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    danger_map: Mapped[str] = mapped_column(Text, default="{}")
    opened_cells: Mapped[str] = mapped_column(Text, default="[]")
    current_position: Mapped[str] = mapped_column(Text, default="{}")
    win_amount: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GameHistory(Base):
    __tablename__ = "game_history"
    __table_args__ = (
        Index("ix_game_history_user_type_created", "user_id", "game_type", "created_at"),
        Index("ix_game_history_type_created", "game_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    game_type: Mapped[str] = mapped_column(String(32), default="frog")
    bet_amount: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(String(16), default="lost")
    multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    win_amount: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RouletteRound(Base):
    __tablename__ = "roulette_rounds"
    __table_args__ = (
        Index("ix_roulette_rounds_chat_status", "chat_id", "status"),
        Index("ix_roulette_rounds_status_ends", "status", "ends_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    result_color: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    result_label: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    total_bet: Mapped[int] = mapped_column(Integer, default=0)
    total_payout: Mapped[int] = mapped_column(Integer, default=0)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RouletteBet(Base):
    __tablename__ = "roulette_bets"
    __table_args__ = (
        UniqueConstraint("round_id", "user_id", name="uq_roulette_bet_user_round"),
        Index("ix_roulette_bets_round", "round_id"),
        Index("ix_roulette_bets_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("roulette_rounds.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="User")
    choice: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(Integer, default=0)
    payout: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActivityScoreEvent(Base):
    __tablename__ = "activity_score_events"
    __table_args__ = (
        Index("ix_activity_score_chat_created", "chat_id", "created_at"),
        Index("ix_activity_score_user_created", "user_telegram_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    game_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_name: Mapped[str] = mapped_column(String(255), default="User")
    points: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(64), default="activity")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CoupleRelationship(Base):
    __tablename__ = "couple_relationships"
    __table_args__ = (
        Index("ix_couple_chat_active", "chat_id", "active"),
        Index("ix_couple_user_one_active", "user_one_telegram_id", "active"),
        Index("ix_couple_user_two_active", "user_two_telegram_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_one_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_one_name: Mapped[str] = mapped_column(String(255), default="User")
    user_two_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_two_name: Mapped[str] = mapped_column(String(255), default="User")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CreditLoan(Base):
    __tablename__ = "credit_loans"
    __table_args__ = (
        Index("ix_credit_loans_user_status", "user_telegram_id", "status"),
        Index("ix_credit_loans_status_due", "status", "due_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    principal: Mapped[int] = mapped_column(Integer)
    interest: Mapped[int] = mapped_column(Integer)
    total_due: Mapped[int] = mapped_column(Integer)
    term_days: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CreditBlockedUser(Base):
    __tablename__ = "credit_blocked_users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), default="User")
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    loan_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BotSetting(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GameLog(Base):
    __tablename__ = "game_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GroupBlacklist(Base):
    __tablename__ = "group_blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    blocked_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GroupSettings(Base):
    __tablename__ = "group_settings"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    giveaway_diamond: Mapped[int] = mapped_column(Integer, default=0)
    giveaway_protection: Mapped[int] = mapped_column(Integer, default=0)
    leave_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    leave_lock_minutes: Mapped[int] = mapped_column(Integer, default=30)
    game_mode: Mapped[str] = mapped_column(String(32), default="normal")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GroupRoleSettings(Base):
    __tablename__ = "group_role_settings"
    __table_args__ = (
        UniqueConstraint("chat_id", "role_key", name="uq_group_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role_key: Mapped[str] = mapped_column(String(64))
    is_allowed: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GroupWeaponSettings(Base):
    __tablename__ = "group_weapon_settings"
    __table_args__ = (
        UniqueConstraint("chat_id", "weapon_key", name="uq_group_weapon"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    weapon_key: Mapped[str] = mapped_column(String(64))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GroupCommandPermissions(Base):
    __tablename__ = "group_command_permissions"
    __table_args__ = (
        UniqueConstraint("chat_id", "command_key", name="uq_group_command"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    command_key: Mapped[str] = mapped_column(String(64))
    permission_level: Mapped[str] = mapped_column(String(32), default="user")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GroupChatPermissions(Base):
    __tablename__ = "group_chat_permissions"
    __table_args__ = (
        UniqueConstraint("chat_id", "phase", name="uq_group_chat_phase"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    phase: Mapped[str] = mapped_column(String(32))
    write_permission: Mapped[str] = mapped_column(String(32), default="all")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GroupTimeSettings(Base):
    __tablename__ = "group_time_settings"
    __table_args__ = (
        UniqueConstraint("chat_id", "time_key", name="uq_group_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    time_key: Mapped[str] = mapped_column(String(64))
    seconds: Mapped[int] = mapped_column(Integer, default=60)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GroupExtraSettings(Base):
    __tablename__ = "group_extra_settings"
    __table_args__ = (
        UniqueConstraint("chat_id", "setting_key", name="uq_group_extra"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    setting_key: Mapped[str] = mapped_column(String(64))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
