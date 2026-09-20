"""SQLAlchemy models. Base.metadata is what alembic autogenerate diffs against."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
