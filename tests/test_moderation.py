"""Test 8: soft delete and restore, on a conversation and on one of its messages."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import STAFF_ROLES
from app.models import Challenge, User


def _learner(client: Callable[..., TestClient], prefix: str) -> str:
    handle = f"{prefix}_{uuid4().hex[:8]}"
    assert client().post("/users", json={"handle": handle}).status_code == 201
    return handle


def _staff_handle(session: Session) -> str:
    return session.scalar(
        select(User.handle).where(User.role.in_(sorted(STAFF_ROLES))).order_by(User.handle)
    )


def _thread(
    session: Session, client: Callable[..., TestClient], prefix: str, replies: int
) -> tuple[int, str]:
    """A conversation with `replies` answers from other learners; returns (id, author)."""
    challenge_id = session.scalar(
        select(Challenge.id).where(Challenge.status == "published").order_by(Challenge.id)
    )
    author = _learner(client, f"{prefix}_author")
    created = client(author).post(
        "/conversations",
        json={
            "challenge_id": challenge_id,
            "topic": "Moderation, from the asker's side",
            "body": "The opening post.",
            "category": "PennyLane Help",
        },
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    for index in range(replies):
        responder = _learner(client, f"{prefix}_{index}")
        response = client(responder).post(
            f"/conversations/{conversation_id}/messages",
            json={"body": f"answer {index}"},
        )
        assert response.status_code == 201
    return conversation_id, author


def test_delete_is_idempotent_and_restore_returns_the_whole_thread(
    db_session: Session, client: Callable[..., TestClient]
) -> None:
    staff = _staff_handle(db_session)
    conversation_id, author = _thread(db_session, client, "del", replies=2)

    before = client(author).get(f"/conversations/{conversation_id}")
    assert before.status_code == 200
    bodies = [message["body"] for message in before.json()["messages"]]

    assert client(staff).delete(f"/conversations/{conversation_id}").status_code == 204
    assert client(staff).delete(f"/conversations/{conversation_id}").status_code == 204
    assert client(author).get(f"/conversations/{conversation_id}").status_code == 404

    restored = client(staff).post(f"/conversations/{conversation_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None

    after = client(author).get(f"/conversations/{conversation_id}")
    assert after.status_code == 200
    assert [message["body"] for message in after.json()["messages"]] == bodies
    assert after.json()["message_count"] == len(bodies)


def test_a_deleted_message_stays_deleted_across_a_conversation_restore(
    db_session: Session, client: Callable[..., TestClient]
) -> None:
    staff = _staff_handle(db_session)
    conversation_id, author = _thread(db_session, client, "msg", replies=2)

    detail = client(author).get(f"/conversations/{conversation_id}").json()
    target = next(m for m in detail["messages"] if m["sequence_no"] == 2)

    path = f"/conversations/{conversation_id}/messages/{target['id']}"
    assert client(staff).delete(path).status_code == 204
    assert client(staff).delete(path).status_code == 204

    def visible_sequences() -> list[int]:
        body = client(author).get(f"/conversations/{conversation_id}").json()
        return [message["sequence_no"] for message in body["messages"]]

    # The gap is the point: deleting a message keeps its sequence_no.
    assert visible_sequences() == [1, 3]

    assert client(staff).delete(f"/conversations/{conversation_id}").status_code == 204
    assert client(staff).post(f"/conversations/{conversation_id}/restore").status_code == 200

    assert visible_sequences() == [1, 3]


def test_restore_of_a_never_deleted_conversation_is_a_no_op(
    db_session: Session, client: Callable[..., TestClient]
) -> None:
    staff = _staff_handle(db_session)
    conversation_id, author = _thread(db_session, client, "noop", replies=1)
    before = client(author).get(f"/conversations/{conversation_id}").json()

    restored = client(staff).post(f"/conversations/{conversation_id}/restore")

    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None
    assert restored.json()["status"] == before["status"]
    assert [m["id"] for m in restored.json()["messages"]] == [
        m["id"] for m in before["messages"]
    ]
