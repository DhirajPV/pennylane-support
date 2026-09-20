"""SQLAlchemy models. Base.metadata is what alembic autogenerate diffs against."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app import enums


class Base(DeclarativeBase):
    pass


def value_check(name: str, column: str, values: tuple[str, ...]) -> CheckConstraint:
    """CHECK (column IN (...)) built from enums.py, so the constraint cannot drift."""
    allowed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


challenge_tags = Table(
    "challenge_tags",
    Base.metadata,
    Column("challenge_id", BigInteger, ForeignKey("challenges.id"), primary_key=True),
    Column("tag_id", BigInteger, ForeignKey("tags.id"), primary_key=True),
    Index("ix_challenge_tags_tag_id", "tag_id"),
)

challenge_prerequisites = Table(
    "challenge_prerequisites",
    Base.metadata,
    Column("challenge_id", BigInteger, ForeignKey("challenges.id"), primary_key=True),
    Column("prerequisite_id", BigInteger, ForeignKey("challenges.id"), primary_key=True),
    CheckConstraint(
        "challenge_id <> prerequisite_id", name="ck_challenge_prerequisites_not_self"
    ),
)

conversation_tags = Table(
    "conversation_tags",
    Base.metadata,
    Column("conversation_id", BigInteger, ForeignKey("conversations.id"), primary_key=True),
    Column("tag_id", BigInteger, ForeignKey("tags.id"), primary_key=True),
    Index("ix_conversation_tags_tag_id", "tag_id"),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    handle: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (value_check("ck_users_role", "role", enums.USER_ROLES),)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class Challenge(Base):
    __tablename__ = "challenges"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(Text, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    attempt_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    average_attempts_to_pass: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    learning_objectives: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    hints: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    author_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    author: Mapped[User | None] = relationship(lazy="raise")
    tags: Mapped[list[Tag]] = relationship(secondary=challenge_tags, lazy="raise")
    prerequisites: Mapped[list[Challenge]] = relationship(
        secondary=challenge_prerequisites,
        primaryjoin=lambda: Challenge.id == challenge_prerequisites.c.challenge_id,
        secondaryjoin=lambda: Challenge.id == challenge_prerequisites.c.prerequisite_id,
        lazy="raise",
    )

    __table_args__ = (
        value_check("ck_challenges_difficulty", "difficulty", enums.CHALLENGE_DIFFICULTIES),
        value_check("ck_challenges_status", "status", enums.CHALLENGE_STATUSES),
        Index("ix_challenges_status_created_at", "status", text("created_at DESC")),
        Index("ix_challenges_category", "category"),
        Index("ix_challenges_difficulty", "difficulty"),
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    challenge_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("challenges.id"), nullable=False
    )
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    priority: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'medium'"))
    is_pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    challenge: Mapped[Challenge] = relationship(lazy="raise")
    author: Mapped[User] = relationship(foreign_keys=[author_id], lazy="raise")
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id], lazy="raise")
    tags: Mapped[list[Tag]] = relationship(secondary=conversation_tags, lazy="raise")
    messages: Mapped[list[Message]] = relationship(
        order_by="Message.sequence_no", lazy="raise"
    )

    __table_args__ = (
        value_check("ck_conversations_status", "status", enums.CONVERSATION_STATUSES),
        value_check("ck_conversations_priority", "priority", enums.CONVERSATION_PRIORITIES),
        Index("ix_conversations_created_at_id", text("created_at DESC"), text("id DESC")),
        Index("ix_conversations_challenge_id_created_at", "challenge_id", text("created_at DESC")),
        Index("ix_conversations_status_created_at", "status", text("created_at DESC")),
        Index(
            "ix_conversations_last_activity_at_id",
            text("last_activity_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_conversations_assignee_open",
            "assignee_id",
            "created_at",
            postgresql_where=text("resolved_at IS NULL AND deleted_at IS NULL"),
        ),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id"), nullable=False
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    posted_as_role: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    upvotes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    helpful_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    is_internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_accepted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_spoiler: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    author: Mapped[User] = relationship(lazy="raise")
    reactions: Mapped[list[MessageReaction]] = relationship(lazy="raise")

    __table_args__ = (
        value_check("ck_messages_posted_as_role", "posted_as_role", enums.USER_ROLES),
        UniqueConstraint(
            "conversation_id", "sequence_no", name="uq_messages_conversation_id_sequence_no"
        ),
        Index("ix_messages_author_id_created_at", "author_id", "created_at"),
        Index(
            "uq_messages_accepted_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("is_accepted"),
        ),
    )


class MessageReaction(Base):
    __tablename__ = "message_reactions"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("messages.id"), primary_key=True
    )
    type: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        value_check("ck_message_reactions_type", "type", enums.REACTION_TYPES),
        Index("ix_message_reactions_message_id", "message_id"),
    )
