"""external_id formatting and reservation (CLAUDE.md "Identifiers")."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

CONVERSATION_PREFIX = "CONV_"
CONVERSATION_MIN_WIDTH = 4


def format_conversation_id(id: int) -> str:
    """'CONV_' + the id padded to at least CONVERSATION_MIN_WIDTH digits.

    The width is greatest(4, length), not a flat 4: Postgres lpad truncates, so a
    flat width would render id 10000 as CONV_1000 and collide with id 1000.
    """
    digits = str(id)
    return f"{CONVERSATION_PREFIX}{digits.zfill(max(CONVERSATION_MIN_WIDTH, len(digits)))}"


def next_conversation_id(db: Session) -> int:
    """Reserve the next conversations.id so one INSERT can carry both id and external_id."""
    return db.scalar(text("SELECT nextval('conversations_id_seq')"))
