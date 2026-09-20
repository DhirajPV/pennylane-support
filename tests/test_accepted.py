"""Test 4: one accepted answer per conversation, whoever replaces it and however it goes.

The API side is asserted through the detail shape; the database side is asserted with
SQL, because the partial unique index is the backstop the API is supposed to respect.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Message

Clients = Callable[..., TestClient]

#: Seeded with role 'support', which is in STAFF_ROLES.
STAFF_HANDLE = "pennylane_support"
CATEGORY = "PennyLane Help"


def _new_learner(client: Clients) -> str:
    """A handle nobody else in the suite holds; POST /users always makes a learner."""
    handle = f"lane_a_{uuid4().hex[:12]}"
    response = client().post("/users", json={"handle": handle})
    assert response.status_code == 201, response.text
    return handle


def _open_conversation(client: Clients, author: str) -> dict:
    # Listed anonymously, so the challenge is published and a learner may open a thread on it.
    challenges = client().get("/challenges", params={"limit": 1})
    assert challenges.status_code == 200, challenges.text

    response = client(author).post(
        "/conversations",
        json={
            "challenge_id": challenges.json()["items"][0]["id"],
            "topic": "Which ansatz for a 6-qubit VQE",
            "body": "I have tried `StronglyEntanglingLayers` and it is slow.",
            "category": CATEGORY,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _post(client: Clients, handle: str, conversation_id: int, **payload: object) -> dict:
    """The created message: a reply answers with itself, not with the thread."""
    response = client(handle).post(
        f"/conversations/{conversation_id}/messages",
        json={"body": "Try a hardware-efficient ansatz first.", **payload},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _message(detail: dict, sequence_no: int) -> dict:
    return next(m for m in detail["messages"] if m["sequence_no"] == sequence_no)


def _accept(client: Clients, handle: str, conversation_id: int, message_id: int):
    return client(handle).post(
        f"/conversations/{conversation_id}/messages/{message_id}/accept"
    )


def _accepted_rows(db_session: Session, conversation_id: int) -> list[int]:
    """The ids the database itself calls accepted, not the ones the API reported."""
    return list(
        db_session.scalars(
            select(Message.id).where(
                Message.conversation_id == conversation_id,
                Message.is_accepted.is_(True),
            )
        )
    )


def _two_replies(client: Clients, conversation_id: int) -> tuple[int, int]:
    helper = _new_learner(client)
    older = _post(client, helper, conversation_id)["id"]
    newer = _post(client, helper, conversation_id)["id"]
    return older, newer


def test_accepting_a_newer_message_replaces_the_older_one(
    client: Clients, db_session: Session
) -> None:
    author = _new_learner(client)
    conversation_id = _open_conversation(client, author)["id"]
    older, newer = _two_replies(client, conversation_id)

    first = _accept(client, author, conversation_id, older)
    assert first.status_code == 200, first.text
    assert first.json()["accepted_message_id"] == older
    assert _accepted_rows(db_session, conversation_id) == [older]

    second = _accept(client, author, conversation_id, newer)
    assert second.status_code == 200, second.text
    assert second.json()["accepted_message_id"] == newer
    assert second.json()["has_accepted"] is True
    assert _accepted_rows(db_session, conversation_id) == [newer]


def test_accepting_an_older_message_replaces_the_newer_one(
    client: Clients, db_session: Session
) -> None:
    author = _new_learner(client)
    conversation_id = _open_conversation(client, author)["id"]
    older, newer = _two_replies(client, conversation_id)

    first = _accept(client, author, conversation_id, newer)
    assert first.status_code == 200, first.text
    assert first.json()["accepted_message_id"] == newer
    assert _accepted_rows(db_session, conversation_id) == [newer]

    second = _accept(client, author, conversation_id, older)
    assert second.status_code == 200, second.text
    assert second.json()["accepted_message_id"] == older
    assert second.json()["has_accepted"] is True
    assert _accepted_rows(db_session, conversation_id) == [older]


def test_deleting_the_accepted_message_clears_the_flag_and_keeps_the_status(
    client: Clients, db_session: Session
) -> None:
    author = _new_learner(client)
    helper = _new_learner(client)
    conversation_id = _open_conversation(client, author)["id"]
    answer_id = _post(client, helper, conversation_id)["id"]

    accepted = _accept(client, author, conversation_id, answer_id)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "answered"

    deleted = client(STAFF_HANDLE).delete(
        f"/conversations/{conversation_id}/messages/{answer_id}"
    )
    assert deleted.status_code == 204, deleted.text

    after = client(author).get(f"/conversations/{conversation_id}")
    assert after.status_code == 200, after.text
    assert after.json()["accepted_message_id"] is None
    assert after.json()["has_accepted"] is False
    # Losing the answer does not un-answer the thread; only a PATCH changes status.
    assert after.json()["status"] == "answered"
    assert _accepted_rows(db_session, conversation_id) == []


def test_accepting_the_opening_message_is_422(client: Clients) -> None:
    author = _new_learner(client)
    opened = _open_conversation(client, author)
    conversation_id = opened["id"]
    _post(client, _new_learner(client), conversation_id)

    refused = _accept(client, author, conversation_id, _message(opened, 1)["id"])
    assert refused.status_code == 422, refused.text


def test_accepting_an_internal_message_is_422(client: Clients) -> None:
    author = _new_learner(client)
    conversation_id = _open_conversation(client, author)["id"]
    internal = _post(client, STAFF_HANDLE, conversation_id, is_internal=True)
    assert internal["is_internal"] is True

    # Staff, because a caller who cannot see the message gets 404 before the 422.
    refused = _accept(client, STAFF_HANDLE, conversation_id, internal["id"])
    assert refused.status_code == 422, refused.text


def test_a_second_accepted_message_set_in_sql_is_rejected_by_the_index(
    client: Clients, db_session: Session
) -> None:
    author = _new_learner(client)
    conversation_id = _open_conversation(client, author)["id"]
    older, newer = _two_replies(client, conversation_id)

    accepted = _accept(client, author, conversation_id, older)
    assert accepted.status_code == 200, accepted.text

    with pytest.raises(IntegrityError):
        db_session.execute(
            update(Message).where(Message.id == newer).values(is_accepted=True)
        )
        db_session.commit()
    db_session.rollback()

    assert _accepted_rows(db_session, conversation_id) == [older]
