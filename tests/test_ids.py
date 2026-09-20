"""Test 7: external_id formatting, and the sequence value an API create carries."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ids import format_conversation_id
from app.models import Challenge


@pytest.mark.parametrize(
    ("id", "external_id"),
    [
        (1, "CONV_0001"),
        (999, "CONV_0999"),
        (9999, "CONV_9999"),
        # The pair a flat lpad(4) would collide: lpad truncates in Postgres, so 10000
        # would render as CONV_1000 and take id 1000's external_id.
        (10000, "CONV_10000"),
        (123456789, "CONV_123456789"),
    ],
)
def test_format_conversation_id_pads_to_at_least_four_digits(
    id: int, external_id: str
) -> None:
    assert format_conversation_id(id) == external_id


def test_create_past_four_digits_keeps_the_id_and_the_external_id_in_step(
    db_session: Session, client: Callable[..., TestClient]
) -> None:
    challenge_id = db_session.scalar(
        select(Challenge.id).where(Challenge.status == "published").order_by(Challenge.id)
    )
    handle = f"ids_{uuid4().hex[:8]}"
    assert client().post("/users", json={"handle": handle}).status_code == 201

    db_session.execute(text("SELECT setval('conversations_id_seq', 9999)"))
    db_session.commit()

    response = client(handle).post(
        "/conversations",
        json={
            "challenge_id": challenge_id,
            "topic": "Does the id survive the fifth digit?",
            "body": "Asking for the sequence.",
            "category": "PennyLane Help",
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["id"] == 10000
    assert created["external_id"] == "CONV_10000"
