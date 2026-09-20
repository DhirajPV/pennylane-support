"""Test 6: the seeded insight totals, and what one new thread moves.

(a) runs on `fresh_db` because the numbers are absolute: on the session database the
other tests have already replied and reacted. (b) is relative, so it takes the shared
database as it finds it and only asserts the delta.
"""

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


def test_seeded_insights(
    fresh_db: Callable[..., TestClient], db_session: Session
) -> None:
    response = fresh_db(_staff_handle(db_session)).get("/insights")

    assert response.status_code == 200
    insights = response.json()

    community = insights["community"]
    assert (
        community["accepted_answers"]["community"],
        community["accepted_answers"]["staff"],
        community["accepted_answers"]["total"],
    ) == (269, 88, 357)
    assert (community["replies"]["community"], community["replies"]["total"]) == (
        1725,
        2261,
    )
    assert (
        community["first_replies"]["community"],
        community["first_replies"]["total"],
    ) == (563, 733)

    assert insights["first_reply"]["no_reply"] == 67
    assert insights["first_reply"]["excluded_negative"] == 140

    assert insights["challenge_load"][0]["external_id"] == "CHAL_015"

    backlog = insights["backlog"]
    assert backlog["unassigned"] == 84
    assert backlog["urgent_unassigned"] == 6
    assert backlog["by_status"]["open"] == 128
    assert backlog["by_status"]["answered"] == 247


def test_one_community_reply_moves_one_number(
    db_session: Session, client: Callable[..., TestClient]
) -> None:
    staff = _staff_handle(db_session)
    before = client(staff).get("/insights").json()["community"]["replies"]

    challenge_id = db_session.scalar(
        select(Challenge.id).where(Challenge.status == "published").order_by(Challenge.id)
    )
    asker = _learner(client, "ins_asker")
    responder = _learner(client, "ins_responder")

    created = client(asker).post(
        "/conversations",
        json={
            "challenge_id": challenge_id,
            "topic": "One reply, one internal note, one reaction twice",
            "body": "The opening post is not a reply.",
            "category": "PennyLane Help",
        },
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    replied = client(responder).post(
        f"/conversations/{conversation_id}/messages",
        json={"body": "Broadcast the parameters instead."},
    )
    assert replied.status_code == 201
    reply_id = next(
        m["id"] for m in replied.json()["messages"] if m["sequence_no"] == 2
    )

    internal = client(staff).post(
        f"/conversations/{conversation_id}/messages",
        json={"body": "Triaged, no action needed.", "is_internal": True},
    )
    assert internal.status_code == 201

    for _ in range(2):
        reaction = client(asker).post(
            f"/conversations/{conversation_id}/messages/{reply_id}/reactions",
            json={"type": "helpful"},
        )
        assert reaction.status_code == 200

    after = client(staff).get("/insights").json()["community"]["replies"]
    assert after["community"] == before["community"] + 1
    # The internal message is a staff reply by every column except the one that counts.
    assert after["staff"] == before["staff"]

    thread = client(asker).get(f"/conversations/{conversation_id}").json()
    reply = next(m for m in thread["messages"] if m["id"] == reply_id)
    assert reply["helpful_count"] == 1
