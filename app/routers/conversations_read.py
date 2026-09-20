"""Conversation reads: the queue, one conversation, and the rest of its messages.

`status` is a filter name here, so fastapi.status is imported under another name.
The write routes live in conversations.py; both mount the same prefix.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import CurrentUser
from app.models import Conversation, User
from app.queries import (
    CONVERSATION_SORTS,
    DETAIL_MESSAGE_LIMIT,
    conversation_list_query,
    load_conversation_detail,
    page_messages,
)
from app.schemas import (
    MAX_SEARCH_CHARS,
    ConversationDetail,
    ConversationListItem,
    ConversationPriority,
    ConversationStatus,
    MessagePage,
    Page,
)
from app.visibility import conversation_filter

router = APIRouter(prefix="/conversations", tags=["conversations"])

QueueSort = Literal[*CONVERSATION_SORTS]

#: page_messages carries the tail of a conversation, so it pages wider than a list.
MESSAGE_PAGE_MAX = 200

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
#: ids are bigserial, so 0 and negatives name nothing: 422 rather than a wasted query
PathId = Annotated[int, Path(gt=0)]
Search = Annotated[str | None, Query(max_length=MAX_SEARCH_CHARS)]


def _visible_or_404(
    db: Session, conversation_id: int, user: User | None, *, include_deleted: bool
) -> None:
    """page_messages returns an empty page for a conversation it cannot see; that is a 404."""
    visible = db.scalar(
        select(Conversation.id).where(
            Conversation.id == conversation_id,
            *conversation_filter(user, include_deleted=include_deleted),
        )
    )
    if visible is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "conversation not found")


@router.get("", response_model=Page[ConversationListItem], summary="List conversations")
def list_conversations(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    q: Search = None,
    status: ConversationStatus | None = None,
    priority: ConversationPriority | None = None,
    assignee: str | None = None,
    challenge_id: int | None = None,
    tag: str | None = None,
    has_accepted: bool | None = None,
    unassigned: bool = False,
    sort: QueueSort = "activity",
    include_deleted: bool = False,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Page[ConversationListItem]:
    """Filters are ANDed. Pinned first, except under sort=priority, which is the queue order."""
    if unassigned and assignee is not None:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            "unassigned=true cannot be combined with assignee",
        )

    items, total = conversation_list_query(
        db,
        user,
        filters={
            "q": q,
            "status": status,
            "priority": priority,
            "assignee": assignee,
            "challenge_id": challenge_id,
            "tag": tag,
            "has_accepted": has_accepted,
            "unassigned": unassigned,
        },
        sort=sort,
        limit=limit,
        offset=offset,
        include_deleted=include_deleted,
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/{conversation_id}", response_model=ConversationDetail, summary="One conversation"
)
def read_conversation(
    conversation_id: PathId,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    include_deleted: bool = False,
    reveal_spoilers: bool = False,
) -> ConversationDetail:
    """First 50 messages by sequence_no. A pure read: view_count is not touched.

    view_count is the snapshot that came with the import, not a live counter. Writing
    to a row on every GET makes the endpoint non-idempotent, turns every read into a
    row lock on the hottest conversations, and still counts only what the API serves.
    A real view count is an events table; README says so.
    """
    detail = load_conversation_detail(
        db,
        conversation_id,
        user,
        include_deleted=include_deleted,
        reveal_spoilers=reveal_spoilers,
    )
    if detail is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "conversation not found")
    return detail


@router.get(
    "/{conversation_id}/messages",
    response_model=MessagePage,
    summary="Page a conversation's messages",
)
def list_messages(
    conversation_id: PathId,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    after_sequence: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=MESSAGE_PAGE_MAX)] = DETAIL_MESSAGE_LIMIT,
    include_deleted: bool = False,
    reveal_spoilers: bool = False,
) -> MessagePage:
    """Keyset over sequence_no: new replies never move a page the caller already read."""
    _visible_or_404(db, conversation_id, user, include_deleted=include_deleted)
    return page_messages(
        db,
        conversation_id,
        user,
        after_sequence=after_sequence,
        limit=limit,
        reveal_spoilers=reveal_spoilers,
        include_deleted=include_deleted,
    )
