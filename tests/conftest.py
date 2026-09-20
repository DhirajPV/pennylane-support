"""Fixtures: one migrated, seeded pennylane_test per session, and an httpx client.

The tests own their database end to end. `make test` runs with SKIP_SEED=1 because the
entrypoint's migrate/seed targets the development database, not this one.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import get_db
from app.main import app
from app.seed.load import seed

ROOT = Path(__file__).resolve().parents[1]

test_engine = create_engine(settings.database_url_test, pool_pre_ping=True, future=True)
TestSessionLocal = sessionmaker(
    bind=test_engine, class_=Session, autoflush=False, expire_on_commit=False
)


def _reset_schema() -> None:
    """Drop everything, so each run starts from the seeded data and nothing else.

    The name check is the guard: this runs DROP SCHEMA, and DATABASE_URL_TEST is one
    typo away from naming the development database.
    """
    database = make_url(settings.database_url_test).database or ""
    if not database.endswith("_test"):
        pytest.exit(f"DATABASE_URL_TEST must name a *_test database, got {database!r}")

    with test_engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


def _migrate() -> None:
    # alembic/env.py reads the URL from settings, so the environment is how the test
    # database gets pointed at.
    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": settings.database_url_test},
        check=True,
    )


@pytest.fixture(scope="session")
def database() -> Iterator[None]:
    _reset_schema()
    _migrate()
    with TestSessionLocal() as session:
        seed(session)

    def override_get_db() -> Iterator[Session]:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def db_session(database: None) -> Iterator[Session]:
    """A session for tests that read or write the database directly."""
    with TestSessionLocal() as session:
        yield session


@pytest.fixture
def client(database: None) -> Iterator[Callable[..., TestClient]]:
    """client() is anonymous; client("handle") sends that handle as X-User."""
    with ExitStack() as stack:

        def make_client(handle: str | None = None) -> TestClient:
            headers = {"X-User": handle} if handle is not None else {}
            return stack.enter_context(TestClient(app, headers=headers))

        yield make_client


@pytest.fixture
def fresh_db(client: Callable[..., TestClient]) -> Callable[..., TestClient]:
    """The seed and nothing else, for tests that assert absolute seeded totals.

    The session database accumulates whatever earlier tests posted, so a test that
    checks "1725 community replies" has to pay for a drop, a migrate and a re-seed.
    The pool is disposed first: DROP SCHEMA waits behind any pooled connection that
    still has a transaction open.
    """
    test_engine.dispose()
    _reset_schema()
    _migrate()
    with TestSessionLocal() as session:
        seed(session)
    return client
