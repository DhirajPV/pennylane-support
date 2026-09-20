"""Every write on a conversation: create, reply, react, accept, patch, delete, restore.

Each handler takes the conversation row with SELECT ... FOR UPDATE, re-reads the state
it decides on inside that transaction, and answers with the detail reloaded after the
commit rather than with the object it just mutated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import enums, ids, queries
from app.db import get_db
from app.deps import RequiredUser, StaffUser
from app.models import Challenge, Conversation, Message, MessageReaction, User
from app.schemas import (
    ConversationDetail,
    CreateConversation,
    CreateMessage,
    PatchConversation,
    ReactionIn,
    ReactionType,
)
from app.visibility import challenge_filter, is_staff, visible_message

router = APIRouter(prefix="/conversations", tags=["conversations"])

DbSession = Annotated[Session, Depends(get_db)]

OPEN, ANSWERED, CLOSED = "open", "answered", "closed"
LEARNER = "learner"

#: Only staff may close, so the author's reachable statuses are the rest.
AUTHOR_STATUSES = frozenset(enums.CONVERSATION_STATUSES) - {CLOSED}
STAFF_ONLY_FIELDS = ("assignee_id", "priority", "is_pinned", "is_locked")


def _now(db: Session) -> datetime:
    """One transaction timestamp for every row a request writes."""
    return db.scalar(select(func.now()))


def _lock(
    db: Session,
    conversation_id: int,
    user: User,
    *,
    allow_deleted: bool = False,
) -> Conversation:
    """The conversation row, locked; its state is only trustworthy after this."""
    conversation = db.scalars(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    ).one_or_none()
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    if conversation.deleted_at is not None and not allow_deleted:
        if not is_staff(user):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
        raise HTTPException(status.HTTP_409_CONFLICT, "restore first")
    return conversation


def _target_message(
    db: Session,
    conversation: Conversation,
    message_id: int,
    user: User,
    *,
    include_deleted: bool = False,
) -> Message:
    """The message a nested path names: 404 unless it is in this conversation and visible."""
    message = db.scalars(
        select(Message).where(
            Message.id == message_id, Message.conversation_id == conversation.id
        )
    ).one_or_none()
    if message is None or not visible_message(message, user, include_deleted=include_deleted):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message not found")
    return message


def _detail(db: Session, conversation_id: int, user: User) -> ConversationDetail:
    detail = queries.load_conversation_detail(db, conversation_id, user)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    return detail


@router.post("", response_model=ConversationDetail, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: CreateConversation, user: RequiredUser, db: DbSession
) -> ConversationDetail:
    challenge_id = db.scalar(
        select(Challenge.id).where(
            Challenge.id == payload.challenge_id, *challenge_filter(user)
        )
    )
    if challenge_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "challenge not found")

    now = _now(db)
    conversation_id = ids.next_conversation_id(db)
    db.add(
        Conversation(
            id=conversation_id,
            external_id=ids.format_conversation_id(conversation_id),
            topic=payload.topic,
            category=payload.category,
            challenge_id=challenge_id,
            author_id=user.id,
            priority=payload.priority,
            created_at=now,
            last_activity_at=now,
        )
    )
    db.add(
        Message(
            conversation_id=conversation_id,
            sequence_no=1,
            author_id=user.id,
            posted_as_role=user.role,
            body=payload.body,
            created_at=now,
        )
    )
    db.commit()
    return _detail(db, conversation_id, user)


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_message(
    conversation_id: int, payload: CreateMessage, user: RequiredUser, db: DbSession
) -> ConversationDetail:
    if payload.is_internal and not is_staff(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "internal messages are staff only")

    conversation = _lock(db, conversation_id, user)
    if conversation.is_locked:
        raise HTTPException(status.HTTP_409_CONFLICT, "conversation is locked")

    # Over every message, not the visible ones: the unique constraint counts deleted
    # and internal rows too.
    last_sequence_no = db.scalar(
        select(func.max(Message.sequence_no)).where(
            Message.conversation_id == conversation_id
        )
    )
    now = _now(db)
    db.add(
        Message(
            conversation_id=conversation_id,
            sequence_no=(last_sequence_no or 0) + 1,
            author_id=user.id,
            posted_as_role=user.role,
            body=payload.body,
            is_internal=payload.is_internal,
            created_at=now,
        )
    )
    conversation.last_activity_at = now
    db.commit()
    return _detail(db, conversation_id, user)


@router.post(
    "/{conversation_id}/messages/{message_id}/reactions", response_model=ConversationDetail
)
def add_reaction(
    conversation_id: int,
    message_id: int,
    payload: ReactionIn,
    user: RequiredUser,
    db: DbSession,
) -> ConversationDetail:
    conversation = _lock(db, conversation_id, user)
    message = _target_message(db, conversation, message_id, user)
    db.execute(
        insert(MessageReaction)
        .values(user_id=user.id, message_id=message.id, type=payload.type)
        .on_conflict_do_nothing()
    )
    db.commit()
    return _detail(db, conversation_id, user)


@router.delete(
    "/{conversation_id}/messages/{message_id}/reactions/{reaction_type}",
    response_model=ConversationDetail,
)
def remove_reaction(
    conversation_id: int,
    message_id: int,
    reaction_type: ReactionType,
    user: RequiredUser,
    db: DbSession,
) -> ConversationDetail:
    conversation = _lock(db, conversation_id, user)
    message = _target_message(db, conversation, message_id, user)
    db.execute(
        delete(MessageReaction).where(
            MessageReaction.user_id == user.id,
            MessageReaction.message_id == message.id,
            MessageReaction.type == reaction_type,
        )
    )
    db.commit()
    return _detail(db, conversation_id, user)


@router.post(
    "/{conversation_id}/messages/{message_id}/accept", response_model=ConversationDetail
)
def accept_message(
    conversation_id: int, message_id: int, user: RequiredUser, db: DbSession
) -> ConversationDetail:
    conversation = _lock(db, conversation_id, user)
    message = _target_message(db, conversation, message_id, user)

    staff = is_staff(user)
    if not staff and conversation.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only the author or staff may accept")
    if not staff and conversation.status == CLOSED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "conversation is closed")
    if conversation.is_locked:
        raise HTTPException(status.HTTP_409_CONFLICT, "conversation is locked")
    if message.sequence_no == 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "the opening message cannot be accepted"
        )
    if message.is_internal:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "an internal message cannot be accepted"
        )

    previous = db.scalars(
        select(Message).where(
            Message.conversation_id == conversation_id, Message.is_accepted.is_(True)
        )
    ).one_or_none()
    if previous is not None:
        previous.is_accepted = False
        # The partial unique index sees statement order, not assignment order.
        db.flush()
    message.is_accepted = True
    if conversation.status == OPEN:
        conversation.status = ANSWERED
    db.commit()
    return _detail(db, conversation_id, user)


def _authorize_author_patch(
    conversation: Conversation, user: User, changes: dict[str, Any]
) -> None:
    if conversation.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your conversation")
    if any(field in changes for field in STAFF_ONLY_FIELDS):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "staff only")
    if "status" not in changes:
        return
    if changes["status"] not in AUTHOR_STATUSES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only staff may close a conversation")
    if conversation.status == CLOSED or conversation.is_locked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "conversation is closed or locked")


def _validate_assignee(db: Session, assignee_id: int | None) -> None:
    if assignee_id is None:
        return
    role = db.scalar(select(User.role).where(User.id == assignee_id))
    if role is None or role == LEARNER:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "assignee must be a non-learner user"
        )


def _apply_status(conversation: Conversation, new_status: str, now: datetime) -> None:
    was_terminal = conversation.status in enums.TERMINAL_STATUSES
    is_terminal = new_status in enums.TERMINAL_STATUSES
    conversation.status = new_status
    if is_terminal and not was_terminal:
        conversation.resolved_at = now
    elif was_terminal and not is_terminal:
        conversation.resolved_at = None
    # resolved -> closed keeps the first resolution time.


@router.patch("/{conversation_id}", response_model=ConversationDetail)
def patch_conversation(
    conversation_id: int, payload: PatchConversation, user: RequiredUser, db: DbSession
) -> ConversationDetail:
    conversation = _lock(db, conversation_id, user)
    # assignee_id is the one field whose null is a value: it unassigns.
    changes = {
        field: getattr(payload, field)
        for field in payload.model_fields_set
        if field == "assignee_id" or getattr(payload, field) is not None
    }
    if not is_staff(user):
        _authorize_author_patch(conversation, user, changes)
    if "assignee_id" in changes:
        _validate_assignee(db, changes["assignee_id"])

    if "status" in changes:
        _apply_status(conversation, changes.pop("status"), _now(db))
    for field, value in changes.items():
        setattr(conversation, field, value)
    db.commit()
    return _detail(db, conversation_id, user)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, staff: StaffUser, db: DbSession) -> Response:
    conversation = _lock(db, conversation_id, staff, allow_deleted=True)
    if conversation.deleted_at is None:
        conversation.deleted_at = _now(db)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{conversation_id}/restore", response_model=ConversationDetail)
def restore_conversation(
    conversation_id: int, staff: StaffUser, db: DbSession
) -> ConversationDetail:
    conversation = _lock(db, conversation_id, staff, allow_deleted=True)
    conversation.deleted_at = None
    db.commit()
    return _detail(db, conversation_id, staff)


@router.delete(
    "/{conversation_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_message(
    conversation_id: int, message_id: int, staff: StaffUser, db: DbSession
) -> Response:
    conversation = _lock(db, conversation_id, staff)
    # include_deleted, so deleting twice is 204 rather than 404.
    message = _target_message(db, conversation, message_id, staff, include_deleted=True)
    if message.deleted_at is None:
        message.deleted_at = _now(db)
        message.is_accepted = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
