"""POST /users (self-serve signup) and the staff assignment picker."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import StaffUser
from app.models import User
from app.schemas import CreateUser, UserOut, UserRole

router = APIRouter(prefix="/users", tags=["users"])

DbSession = Annotated[Session, Depends(get_db)]

HANDLE_PATTERN = re.compile(r"[a-z0-9_]{3,40}")
LEARNER = "learner"


@router.get("", response_model=list[UserOut], summary="Assignment picker (staff only)")
def list_users(db: DbSession, _staff: StaffUser, role: UserRole | None = None) -> list[User]:
    conditions = [User.role == role] if role is not None else []
    return list(db.scalars(select(User).where(*conditions).order_by(User.handle)).all())


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
