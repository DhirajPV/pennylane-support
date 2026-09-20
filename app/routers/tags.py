"""GET /tags: the whole tag vocabulary with its visibility-filtered usage counts."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import CurrentUser
from app.queries import tag_counts_query
from app.schemas import Page, TagWithCounts

router = APIRouter(tags=["tags"])

#: the vocabulary is small and browsed whole, so it pages wider than a content list
TAG_LIMIT_MAX = 200
TAG_LIMIT_DEFAULT = 100


@router.get("/tags", response_model=Page[TagWithCounts], summary="Tags with usage counts")
def list_tags(
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=TAG_LIMIT_MAX)] = TAG_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[TagWithCounts]:
    """Ordered by conversation_count desc, then challenge_count desc, then name."""
    items, total = tag_counts_query(db, user, limit=limit, offset=offset)
    return Page(items=items, total=total, limit=limit, offset=offset)
