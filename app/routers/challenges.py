"""Challenge reads: the catalogue, one challenge, and the conversations opened on it.

`status` is a filter name on two of these routes, so fastapi.status is imported
under another name. Non-staff never see a non-published challenge: challenge_filter
drops it from the list, and both single-challenge routes 404 rather than confirm it
exists.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import CurrentUser
from app.models import Challenge
from app.queries import (
    CONVERSATION_SORTS,
    challenge_list_query,
    conversation_list_query,
    load_challenge,
)
from app.schemas import (
    ChallengeDetail,
    ChallengeDifficulty,
    ChallengeListItem,
    ChallengeStatus,
    ConversationListItem,
    ConversationStatus,
    Page,
)
from app.visibility import challenge_filter

router = APIRouter(prefix="/challenges", tags=["challenges"])

#: sort=priority is the support queue's ordering; inside one challenge it is noise.
BrowseSort = Literal[*tuple(sort for sort in CONVERSATION_SORTS if sort != "priority")]

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("", response_model=Page[ChallengeListItem], summary="List challenges")
def list_challenges(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    category: str | None = None,
    difficulty: ChallengeDifficulty | None = None,
    tag: str | None = None,
    status: ChallengeStatus | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Page[ChallengeListItem]:
    """Filters are ANDed. Non-staff see published challenges only, whatever `status` asks for."""
    items, total = challenge_list_query(
        db,
        user,
        filters={
            "category": category,
            "difficulty": difficulty,
            "tag": tag,
            "status": status,
        },
        limit=limit,
        offset=offset,
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{challenge_id}", response_model=ChallengeDetail, summary="One challenge")
def read_challenge(
    challenge_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
) -> ChallengeDetail:
    """404 to non-staff when the challenge is not published."""
    challenge = load_challenge(db, challenge_id, user)
    if challenge is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "challenge not found")
    return challenge


@router.get(
    "/{challenge_id}/conversations",
    response_model=Page[ConversationListItem],
    summary="Conversations opened on one challenge",
)
def list_challenge_conversations(
    challenge_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    q: str | None = None,
    status: ConversationStatus | None = None,
    has_accepted: bool | None = None,
    sort: BrowseSort = "activity",
    include_deleted: bool = False,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Page[ConversationListItem]:
    """Pinned first, then `sort`. 404 when the challenge itself is not visible."""
    visible = db.scalar(
        select(Challenge.id).where(Challenge.id == challenge_id, *challenge_filter(user))
    )
    if visible is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "challenge not found")

    items, total = conversation_list_query(
        db,
        user,
        filters={
            "q": q,
            "status": status,
            "has_accepted": has_accepted,
            "challenge_id": challenge_id,
        },
        sort=sort,
        limit=limit,
        offset=offset,
        include_deleted=include_deleted,
    )
    return Page(items=items, total=total, limit=limit, offset=offset)
