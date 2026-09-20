"""A message body is stored exactly as it was sent.

Four leading spaces are a CommonMark code block and the trailing newline is the author's,
so a validator that strips changes what the reply means.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from fastapi.testclient import TestClient

Clients = Callable[..., TestClient]

BODY = "    dev = qml.device('default.qubit', wires=2)\n"


def test_a_reply_body_is_read_back_byte_identical(client: Clients) -> None:
    handle = f"content_{uuid4().hex[:12]}"
    assert client().post("/users", json={"handle": handle}).status_code == 201

    challenges = client().get("/challenges", params={"limit": 1})
    assert challenges.status_code == 200, challenges.text
    created = client(handle).post(
        "/conversations",
        json={
            "challenge_id": challenges.json()["items"][0]["id"],
            "topic": "Does a reply keep its indentation",
            "body": "Asking before I paste code.",
            "category": "PennyLane Help",
        },
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    reply = client(handle).post(
        f"/conversations/{conversation_id}/messages", json={"body": BODY}
    )
    assert reply.status_code == 201, reply.text
    assert reply.json()["body"].encode() == BODY.encode()

    page = client().get(
        f"/conversations/{conversation_id}/messages",
        params={"after_sequence": 1, "limit": 1},
    )
    assert page.status_code == 200, page.text
    [read_back] = page.json()["items"]
    assert read_back["id"] == reply.json()["id"]
    assert read_back["body"].encode() == BODY.encode()
