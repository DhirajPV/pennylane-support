"""Current user from the X-User header. No real auth; see README for where it enters."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import enums
from app.db import get_db
from app.models import User


def get_current_user(
    x_user: str | None = Header(None),
    db: Session = Depends(get_db),
) -> User | None:
    """The users row named by X-User, or None when the header is absent.

    A header that names no user is 401 on every request, reads included: a bad
    handle is a client bug, not anonymous.
    """
    if x_user is None:
        return None
    user = db.scalar(select(User).where(User.handle == x_user))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"unknown user: {x_user}")
    return user


CurrentUser = Annotated[User | None, Depends(get_current_user)]


def require_user(user: CurrentUser) -> User:
    """Writes need a user."""
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-User header required")
    return user


RequiredUser = Annotated[User, Depends(require_user)]


def require_staff(user: RequiredUser) -> User:
    """Authorization stays keyed on users.role, so the permission tests survive real auth."""
    if user.role not in enums.STAFF_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "staff only")
    return user


StaffUser = Annotated[User, Depends(require_staff)]
