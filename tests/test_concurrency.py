"""Test 5: ten threads reply to one conversation at once; no sequence number is lost.

The row lock in the write router is the mechanism under test, so the requests have to
overlap for real. Each worker gets its own TestClient: a client entered as a context
manager owns one blocking portal, so ten clients are ten event loops rather than ten
turns through one.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Challenge

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
