"""POST /users (self-serve signup) and the staff assignment picker."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import StaffUser
from app.models import User
from app.schemas import CreateUser, Page, UserOut, UserRole

router = APIRouter(prefix="/users", tags=["users"])

DbSession = Annotated[Session, Depends(get_db)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]

HANDLE_PATTERN = re.compile(r"[a-z0-9_]{3,40}")
LEARNER = "learner"


@router.get("", response_model=Page[UserOut], summary="Assignment picker (staff only)")
def list_users(
    db: DbSession,
    _staff: StaffUser,
    role: UserRole | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Page[UserOut]:
    conditions = [User.role == role] if role is not None else []
    total = db.scalar(select(func.count()).select_from(User).where(*conditions))
    users = db.scalars(
        select(User).where(*conditions).order_by(User.handle).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[UserOut.model_validate(user) for user in users],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a learner; no auth needed",
)
def create_user(payload: CreateUser, db: DbSession) -> User:
    if HANDLE_PATTERN.fullmatch(payload.handle) is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "handle must match [a-z0-9_]{3,40}"
        )

    user = User(handle=payload.handle, role=LEARNER)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # The unique index is the check: a SELECT first would still race.
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"handle already taken: {payload.handle}"
        ) from None
    return user
