"""Test 3: what internal messages, foreign message ids and soft deletes hide, and from whom.

Every assertion here is a negative one about a row that exists: the caller either cannot
see it or is told it is not there. Each test builds its own conversation.
"""

from __future__ import annotations

from collections.abc import Callable
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


def _open_conversation(client: Clients, author: str) -> dict:
    # Listed anonymously, so the challenge is published and a learner may open a thread on it.
    challenges = client().get("/challenges", params={"limit": 1})
    assert challenges.status_code == 200, challenges.text

    response = client(author).post(
        "/conversations",
        json={
            "challenge_id": challenges.json()["items"][0]["id"],
            "topic": "Shots keep changing my expectation value",
            "body": "Is `shots=None` the analytic device?",
            "category": CATEGORY,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _post(client: Clients, handle: str, conversation_id: int, **payload: object) -> dict:
    response = client(handle).post(
        f"/conversations/{conversation_id}/messages",
        json={"body": "See the note below.", **payload},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _message(detail: dict, sequence_no: int) -> dict:
    return next(m for m in detail["messages"] if m["sequence_no"] == sequence_no)


def _page_ids(client: Clients, handle: str | None, conversation_id: int) -> list[int]:
    page = client(handle).get(f"/conversations/{conversation_id}/messages")
    assert page.status_code == 200, page.text
    return [message["id"] for message in page.json()["items"]]


def test_an_internal_message_is_invisible_to_anonymous_and_learner_callers(
    client: Clients,
) -> None:
    author = _new_learner(client)
    opened = _open_conversation(client, author)
    conversation_id = opened["id"]
    opening_id = _message(opened, 1)["id"]
    internal = _message(_post(client, STAFF_HANDLE, conversation_id, is_internal=True), 2)
    internal_id = internal["id"]
    assert internal["is_internal"] is True

    for handle in (None, author):
        detail = client(handle).get(f"/conversations/{conversation_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["message_count"] == 1
        assert [m["id"] for m in detail.json()["messages"]] == [opening_id]
        assert _page_ids(client, handle, conversation_id) == [opening_id]

    staff_detail = client(STAFF_HANDLE).get(f"/conversations/{conversation_id}")
    assert staff_detail.status_code == 200, staff_detail.text
    assert staff_detail.json()["message_count"] == 2
    assert [m["id"] for m in staff_detail.json()["messages"]] == [opening_id, internal_id]
    assert _page_ids(client, STAFF_HANDLE, conversation_id) == [opening_id, internal_id]


def test_a_learner_holding_an_internal_message_id_cannot_reach_it(client: Clients) -> None:
    # The learner is the conversation's author, so nothing here can be a 403 about
    # ownership: whatever is refused is refused because the message is not visible.
    author = _new_learner(client)
    conversation_id = _open_conversation(client, author)["id"]
    internal = _message(_post(client, STAFF_HANDLE, conversation_id, is_internal=True), 2)
    internal_id = internal["id"]
    assert internal["is_internal"] is True

    # There is no route that reads one message, so "not found" shows up on the page as
    # absence and on the two nested write routes as a 404.
    assert internal_id not in _page_ids(client, author, conversation_id)

    reaction = client(author).post(
        f"/conversations/{conversation_id}/messages/{internal_id}/reactions",
        json={"type": "helpful"},
    )
    assert reaction.status_code == 404, reaction.text

    # 404, not the 422 an internal message would earn from a caller who can see it.
    accept = client(author).post(
        f"/conversations/{conversation_id}/messages/{internal_id}/accept"
    )
    assert accept.status_code == 404, accept.text


def test_a_message_id_from_another_conversation_is_404(client: Clients) -> None:
    author = _new_learner(client)
    helper = _new_learner(client)

    conversation_id = _open_conversation(client, author)["id"]
    other_id = _open_conversation(client, author)["id"]
    foreign_id = _message(_post(client, helper, other_id), 2)["id"]

    # Nested message ids only appear on writes, which need an X-User; anonymous never
    # gets as far as the id.
    for handle in (author, STAFF_HANDLE):
        reaction = client(handle).post(
            f"/conversations/{conversation_id}/messages/{foreign_id}/reactions",
            json={"type": "upvote"},
        )
        assert reaction.status_code == 404, reaction.text

        accept = client(handle).post(
            f"/conversations/{conversation_id}/messages/{foreign_id}/accept"
        )
        assert accept.status_code == 404, accept.text

    deletion = client(STAFF_HANDLE).delete(
        f"/conversations/{conversation_id}/messages/{foreign_id}"
    )
    assert deletion.status_code == 404, deletion.text


def test_a_deleted_conversation_is_404_to_a_learner_and_readable_by_staff(
    client: Clients,
) -> None:
    author = _new_learner(client)
    staff = client(STAFF_HANDLE)
    conversation_id = _open_conversation(client, author)["id"]

    deleted = staff.delete(f"/conversations/{conversation_id}")
    assert deleted.status_code == 204, deleted.text

    for handle in (None, author):
        assert client(handle).get(f"/conversations/{conversation_id}").status_code == 404
        # include_deleted is staff's flag; asking for it does not grant it.
        asked = client(handle).get(
            f"/conversations/{conversation_id}", params={"include_deleted": "true"}
        )
        assert asked.status_code == 404, asked.text

    assert staff.get(f"/conversations/{conversation_id}").status_code == 404

    revealed = staff.get(
        f"/conversations/{conversation_id}", params={"include_deleted": "true"}
    )
    assert revealed.status_code == 200, revealed.text
    assert revealed.json()["deleted_at"] is not None


def test_message_count_follows_the_caller_after_a_message_is_deleted(
    client: Clients,
) -> None:
    author = _new_learner(client)
    helper = _new_learner(client)
    staff = client(STAFF_HANDLE)

    conversation_id = _open_conversation(client, author)["id"]
    doomed_id = _message(_post(client, helper, conversation_id), 2)["id"]
    _post(client, helper, conversation_id)

    before = client(author).get(f"/conversations/{conversation_id}")
    assert before.json()["message_count"] == 3

    deleted = staff.delete(f"/conversations/{conversation_id}/messages/{doomed_id}")
    assert deleted.status_code == 204, deleted.text

    after = client(author).get(f"/conversations/{conversation_id}")
    assert after.status_code == 200, after.text
    assert after.json()["message_count"] == 2
    assert doomed_id not in [m["id"] for m in after.json()["messages"]]
    assert doomed_id not in _page_ids(client, author, conversation_id)

    assert staff.get(f"/conversations/{conversation_id}").json()["message_count"] == 2

    revealed = staff.get(
        f"/conversations/{conversation_id}", params={"include_deleted": "true"}
    )
    assert revealed.status_code == 200, revealed.text
    assert revealed.json()["message_count"] == 3
    assert doomed_id in [m["id"] for m in revealed.json()["messages"]]
