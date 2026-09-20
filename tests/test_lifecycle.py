"""Test 2: the walk from open to closed, and what a closed or locked conversation refuses.

Every test opens the conversation it uses, so nothing here depends on the order the
suite runs in or on rows another test left behind.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

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


def _assignable_id(client: Clients) -> int:
    page = client(STAFF_HANDLE).get("/users", params={"role": "mentor", "limit": 1})
    assert page.status_code == 200, page.text
    return page.json()["items"][0]["id"]


def _open_conversation(client: Clients, author: str) -> dict:
    # Listed anonymously, so the challenge is published and a learner may open a thread on it.
    challenges = client().get("/challenges", params={"limit": 1})
    assert challenges.status_code == 200, challenges.text

    response = client(author).post(
        "/conversations",
        json={
            "challenge_id": challenges.json()["items"][0]["id"],
            "topic": "qml.grad returns all zeros",
            "body": "My cost function is flat. What am I missing?",
            "category": CATEGORY,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _reply(client: Clients, handle: str, conversation_id: int) -> dict:
    response = client(handle).post(
        f"/conversations/{conversation_id}/messages",
        json={"body": "Your parameters are probably not `requires_grad`."},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _message(detail: dict, sequence_no: int) -> dict:
    return next(m for m in detail["messages"] if m["sequence_no"] == sequence_no)


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_open_to_closed_resolves_once(client: Clients) -> None:
    author = _new_learner(client)
    helper = _new_learner(client)
    staff = client(STAFF_HANDLE)

    opened = _open_conversation(client, author)
    conversation_id = opened["id"]
    assert opened["status"] == "open"
    assert opened["resolved_at"] is None

    answer_id = _message(_reply(client, helper, conversation_id), 2)["id"]

    accepted = client(author).post(
        f"/conversations/{conversation_id}/messages/{answer_id}/accept"
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "answered"
    assert accepted.json()["accepted_message_id"] == answer_id
    # Accepting answers the thread; it does not resolve it.
    assert accepted.json()["resolved_at"] is None

    assignee_id = _assignable_id(client)
    assigned = staff.patch(
        f"/conversations/{conversation_id}", json={"assignee_id": assignee_id}
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assignee"]["id"] == assignee_id
    assert assigned.json()["status"] == "answered"

    resolved = staff.patch(f"/conversations/{conversation_id}", json={"status": "resolved"})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"
    resolved_at = resolved.json()["resolved_at"]
    assert resolved_at is not None

    closed = staff.patch(f"/conversations/{conversation_id}", json={"status": "closed"})
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    assert closed.json()["resolved_at"] == resolved_at


def test_reopening_clears_resolved_at_and_resolving_again_sets_a_new_one(
    client: Clients,
) -> None:
    author = _new_learner(client)
    staff = client(STAFF_HANDLE)
    conversation_id = _open_conversation(client, author)["id"]

    first = staff.patch(f"/conversations/{conversation_id}", json={"status": "resolved"})
    assert first.status_code == 200, first.text
    first_resolved_at = first.json()["resolved_at"]
    assert first_resolved_at is not None

    reopened = staff.patch(f"/conversations/{conversation_id}", json={"status": "open"})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "open"
    assert reopened.json()["resolved_at"] is None

    again = staff.patch(f"/conversations/{conversation_id}", json={"status": "resolved"})
    assert again.status_code == 200, again.text
    assert again.json()["resolved_at"] is not None
    assert _moment(again.json()["resolved_at"]) > _moment(first_resolved_at)


def test_the_author_cannot_change_the_status_of_a_closed_conversation(
    client: Clients,
) -> None:
    author = _new_learner(client)
    staff = client(STAFF_HANDLE)
    conversation_id = _open_conversation(client, author)["id"]

    closed = staff.patch(f"/conversations/{conversation_id}", json={"status": "closed"})
    assert closed.status_code == 200, closed.text

    refused = client(author).patch(
        f"/conversations/{conversation_id}", json={"status": "open"}
    )
    assert refused.status_code == 403, refused.text
    # Staff reopening it is the way back, so the refusal is about the caller, not the row.
    reopened = staff.patch(f"/conversations/{conversation_id}", json={"status": "open"})
    assert reopened.status_code == 200, reopened.text


def test_a_locked_conversation_refuses_a_reply_but_allows_a_reaction(
    client: Clients,
) -> None:
    author = _new_learner(client)
    helper = _new_learner(client)
    staff = client(STAFF_HANDLE)

    conversation_id = _open_conversation(client, author)["id"]
    answer_id = _message(_reply(client, helper, conversation_id), 2)["id"]

    locked = staff.patch(f"/conversations/{conversation_id}", json={"is_locked": True})
    assert locked.status_code == 200, locked.text
    assert locked.json()["is_locked"] is True

    late_reply = client(helper).post(
        f"/conversations/{conversation_id}/messages", json={"body": "One more thought."}
    )
    assert late_reply.status_code == 409, late_reply.text

    reaction = client(author).post(
        f"/conversations/{conversation_id}/messages/{answer_id}/reactions",
        json={"type": "helpful"},
    )
    assert reaction.status_code == 200, reaction.text
    assert _message(reaction.json(), 2)["my_reactions"] == ["helpful"]
