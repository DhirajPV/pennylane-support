"""Test 1: the seed is idempotent, and a re-seed never overwrites user-mutable data."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Conversation
from app.seed.load import COUNTED_TABLES, seed


def _counts(session: Session) -> dict[str, int]:
    return {
        table.name: session.scalar(select(func.count()).select_from(table))
        for table in COUNTED_TABLES
    }


def _status(session: Session, external_id: str) -> str:
    return session.scalar(
        select(Conversation.status).where(Conversation.external_id == external_id)
    )


def test_seed_skips_when_conversations_has_rows(db_session: Session) -> None:
    """The conftest fixture already seeded, so an unforced run is a no-op."""
    assert seed(db_session).skipped is True


def test_second_seed_changes_no_row_counts(db_session: Session) -> None:
    before = _counts(db_session)

    report = seed(db_session, force=True)

    assert report.skipped is False
    assert _counts(db_session) == before


def test_reseed_keeps_a_status_set_after_the_seed(db_session: Session) -> None:
    # CONV_0001 ships closed, so closing it proves nothing on its own. The second
    # conversation is open in the source data, which is what makes this bite: it only
    # stays closed if conversations are ON CONFLICT DO NOTHING.
    still_open = db_session.scalar(
        select(Conversation.external_id)
        .where(Conversation.status == "open")
        .order_by(Conversation.external_id)
        .limit(1)
    )
    before = {name: _status(db_session, name) for name in ("CONV_0001", still_open)}

    db_session.execute(
        update(Conversation)
        .where(Conversation.external_id.in_(before))
        .values(status="closed")
    )
    db_session.commit()

    try:
        seed(db_session, force=True)

        assert _status(db_session, "CONV_0001") == "closed"
        assert _status(db_session, still_open) == "closed"
    finally:
        # Leave the seeded statuses as the rest of the suite expects to find them.
        for external_id, status in before.items():
            db_session.execute(
                update(Conversation)
                .where(Conversation.external_id == external_id)
                .values(status=status)
            )
        db_session.commit()
