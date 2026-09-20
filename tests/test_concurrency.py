"""Test 5: ten threads reply to one conversation at once; no sequence number is lost.

The row lock in the write router is the mechanism under test, so the requests have to
overlap for real. Each worker gets its own TestClient: a client entered as a context
manager owns one blocking portal, so ten clients are ten event loops rather than ten
turns through one.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Challenge, User
from app.routers.conversations_write import create_message
from app.schemas import CreateMessage
from tests.conftest import TestSessionLocal

WORKERS = 10


def _learner(client: Callable[..., TestClient], prefix: str) -> str:
    handle = f"{prefix}_{uuid4().hex[:8]}"
    assert client().post("/users", json={"handle": handle}).status_code == 201
    return handle


def test_ten_concurrent_replies_number_themselves_two_to_eleven(
    db_session: Session, client: Callable[..., TestClient]
) -> None:
    challenge_id = db_session.scalar(
        select(Challenge.id).where(Challenge.status == "published").order_by(Challenge.id)
    )
    author = _learner(client, "conc_author")
    created = client(author).post(
        "/conversations",
        json={
            "challenge_id": challenge_id,
            "topic": "Ten people answer at once",
            "body": "What happens to the sequence numbers?",
            "category": "PennyLane Help",
        },
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    # Built on the main thread: the client fixture's ExitStack is not thread-safe.
    repliers = [
        client(_learner(client, f"conc_{index}")) for index in range(WORKERS)
    ]

    def reply(index: int) -> tuple[int, str]:
        response = repliers[index].post(
            f"/conversations/{conversation_id}/messages",
            json={"body": f"reply from worker {index}"},
        )
        return response.status_code, f"reply from worker {index}"

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(reply, range(WORKERS)))

    assert [status for status, _ in results] == [201] * WORKERS

    page = client().get(
        f"/conversations/{conversation_id}/messages",
        params={"after_sequence": 1, "limit": 200},
    )
    assert page.status_code == 200
    replies = page.json()["items"]
    assert [message["sequence_no"] for message in replies] == list(range(2, WORKERS + 2))
    assert {message["body"] for message in replies} == {body for _, body in results}


def test_a_reply_that_waited_for_the_lock_stamps_the_later_time(
    db_session: Session, client: Callable[..., TestClient]
) -> None:
    """The slow request started first, so now() would stamp it into the past.

    Sessions, not threads: the point is the order the lock is acquired in, and two
    sessions make that order exact. `early` opens its transaction first and only then
    writes, by which time `prompt` has already replied and committed.
    """
    challenge_id = db_session.scalar(
        select(Challenge.id).where(Challenge.status == "published").order_by(Challenge.id)
    )
    author = _learner(client, "clock_author")
    created = client(author).post(
        "/conversations",
        json={
            "challenge_id": challenge_id,
            "topic": "Who stamps last",
            "body": "The opening post.",
            "category": "PennyLane Help",
        },
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    slow = _learner(client, "clock_slow")
    quick = _learner(client, "clock_quick")

    with TestSessionLocal() as early, TestSessionLocal() as prompt:
        early_user = early.scalar(select(User).where(User.handle == slow))
        transaction_start = early.scalar(select(func.now()))
        time.sleep(0.05)

        prompt_user = prompt.scalar(select(User).where(User.handle == quick))
        first_committed = create_message(
            conversation_id, CreateMessage(body="in first"), prompt_user, prompt, Response()
        )

        # Only now does the earlier transaction take the row and write.
        last_committed = create_message(
            conversation_id, CreateMessage(body="in second"), early_user, early, Response()
        )

    assert (first_committed.sequence_no, last_committed.sequence_no) == (2, 3)
    # The whole point: the later writer started its transaction before the earlier one
    # committed, so now() would have put it first.
    assert transaction_start < first_committed.created_at < last_committed.created_at

    thread = client(author).get(f"/conversations/{conversation_id}").json()
    assert thread["last_activity_at"] == last_committed.created_at.isoformat().replace(
        "+00:00", "Z"
    )
