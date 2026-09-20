"""The one place the visibility rules live (CLAUDE.md "Visibility policy").

Two forms of the same rules: a predicate for a row already loaded, and a list of
SQLAlchemy criteria to splice into a query's WHERE. Every read and every mutation
resolves its target through one of them, so "not visible" is 404 everywhere.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement

from app import enums
from app.models import Challenge, Conversation, Message, User

PUBLISHED = "published"

#: 9b returns this instead of a spoiler body. See mask_message.
SPOILER_PLACEHOLDER = "[spoiler hidden. Pass reveal_spoilers=true to view.]"


def is_staff(user: User | None) -> bool:
    return user is not None and user.role in enums.STAFF_ROLES


def _deleted_allowed(user: User | None, include_deleted: bool) -> bool:
    """Soft-deleted rows need both staff and an explicit ask."""
    return include_deleted and is_staff(user)


def visible_message(
    message: Message,
    user: User | None,
    *,
    reveal_spoilers: bool = False,
    include_deleted: bool = False,
) -> bool:
    """Whether `user` may see this message at all.

    reveal_spoilers is part of the read context every caller already carries; it
    does not gate the row, only the body (mask_message). Callers are responsible
    for the conversation: a message is never visible if its conversation is not.
    """
    if message.deleted_at is not None and not _deleted_allowed(user, include_deleted):
        return False
    return not (message.is_internal and not is_staff(user))


def visible_conversation(
    conversation: Conversation,
    user: User | None,
    *,
    include_deleted: bool = False,
) -> bool:
    return conversation.deleted_at is None or _deleted_allowed(user, include_deleted)


def visible_challenge(challenge: Challenge, user: User | None) -> bool:
    return challenge.status == PUBLISHED or is_staff(user)


def message_filter(
    user: User | None,
    *,
    reveal_spoilers: bool = False,
    include_deleted: bool = False,
) -> list[ColumnElement[bool]]:
    """visible_message as query criteria."""
    criteria: list[ColumnElement[bool]] = []
    if not _deleted_allowed(user, include_deleted):
        criteria.append(Message.deleted_at.is_(None))
    if not is_staff(user):
        criteria.append(Message.is_internal.is_(False))
    return criteria


def conversation_filter(
    user: User | None,
    *,
    include_deleted: bool = False,
) -> list[ColumnElement[bool]]:
    criteria: list[ColumnElement[bool]] = []
    if not _deleted_allowed(user, include_deleted):
        criteria.append(Conversation.deleted_at.is_(None))
    return criteria


def challenge_filter(user: User | None) -> list[ColumnElement[bool]]:
    """Non-staff see published challenges only."""
    return [] if is_staff(user) else [Challenge.status == PUBLISHED]


def mask_message(message: Message, user: User | None, reveal_spoilers: bool) -> str:
    """The body to show for a message the caller may already see.

    Hook for the 9b spoiler wall: 9b returns SPOILER_PLACEHOLDER when
    message.is_spoiler and not reveal_spoilers. Every read path routes its body
    through here, so switching the wall on is a change to this function alone.
    """
    return message.body
