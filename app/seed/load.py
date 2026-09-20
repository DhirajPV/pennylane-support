"""Idempotent seed from data/*.json.

Reference data (users, tags, challenges and their join tables) is upserted, so a data
refresh lands. User-mutable data (conversations, messages, conversation_tags) is
insert-or-ignore, so anything changed through the API survives a re-seed. Either way a
second run changes no row counts.

The entrypoint runs this on every container start, so it skips itself once conversations
has rows unless FORCE_SEED=1.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Table, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import enums
from app.models import (
    Challenge,
    Conversation,
    Message,
    Tag,
    User,
    challenge_prerequisites,
    challenge_tags,
    conversation_tags,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CHALLENGES_FILE = DATA_DIR / "pennylane_coding_challenges.json"
CONVERSATIONS_FILE = DATA_DIR / "pennylane_support_conversations.json"

# enums.USER_ROLES is ordered least to most privileged, which is exactly the tie-break
# order the role rule asks for.
ROLE_PRIVILEGE = {role: rank for rank, role in enumerate(enums.USER_ROLES)}
UNPUBLISHED = frozenset(enums.CHALLENGE_STATUSES) - {"published"}

COUNTED_TABLES: tuple[Table, ...] = (
    User.__table__,
    Tag.__table__,
    Challenge.__table__,
    challenge_tags,
    challenge_prerequisites,
    Conversation.__table__,
    Message.__table__,
    conversation_tags,
)

ANOMALIES: tuple[tuple[str, str], ...] = (
    ("points_coerced", "challenge points coerced from string"),
    ("non_monotonic_posts", "conversations with non-monotonic post timestamps"),
    ("last_post_before_first", "conversations whose last post predates the first"),
    ("resolution_before_last_post", "created_at + resolution_time_hours < last post timestamp"),
    ("multi_role_users", "users with more than one role"),
    ("on_unpublished_challenge", "conversations on draft/archived challenges"),
    ("before_challenge", "conversations created before their challenge"),
    ("unpublished_prerequisite", "published challenges with a draft/archived prerequisite"),
)


@dataclass
class SeedReport:
    skipped: bool
    counts: dict[str, int]
    anomalies: dict[str, int] = field(default_factory=dict)


def _read(path: Path, key: str) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)[key]


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _role_for(counts: Counter[str]) -> str:
    """Most frequent role; ties break toward the lower-privilege one."""
    if not counts:
        return "learner"
    return min(counts.items(), key=lambda item: (-item[1], ROLE_PRIVILEGE[item[0]]))[0]


def _points(raw: Any, external_id: str, anomalies: dict[str, int]) -> int:
    if isinstance(raw, int):
        return raw
    logger.warning("%s: points %r is not an int, coercing", external_id, raw)
    anomalies["points_coerced"] += 1
    return int(raw)


def _upsert(session: Session, table: Table, rows: list[dict[str, Any]], *, key: list[str],
            update: list[str]) -> None:
    """Reference data: INSERT ... ON CONFLICT DO UPDATE, batched."""
    if not rows:
        return
    stmt = insert(table)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=key, set_={name: stmt.excluded[name] for name in update}
        ),
        rows,
    )


def _insert_ignore(session: Session, table: Table, rows: list[dict[str, Any]], *,
                   key: list[str] | None = None) -> None:
    """User-mutable data: INSERT ... ON CONFLICT DO NOTHING, batched."""
    if not rows:
        return
    session.execute(insert(table).on_conflict_do_nothing(index_elements=key), rows)


def _id_map(session: Session, table: Table, key: str) -> dict[str, int]:
    return {value: row_id for row_id, value in session.execute(select(table.c.id, table.c[key]))}


def _row_counts(session: Session) -> dict[str, int]:
    return {
        table.name: session.scalar(select(func.count()).select_from(table))
        for table in COUNTED_TABLES
    }


def seed(session: Session, *, force: bool = False) -> SeedReport:
    """Load data/*.json into the database in one transaction. Safe to run repeatedly."""
    if not force and session.scalar(select(Conversation.id).limit(1)) is not None:
        print("seed: conversations already has rows, skipping. FORCE_SEED=1 re-runs it.")
        return SeedReport(skipped=True, counts=_row_counts(session))

    challenges = _read(CHALLENGES_FILE, "coding_challenges")
    conversations = _read(CONVERSATIONS_FILE, "support_conversations")
    challenge_by_external = {row["challenge_id"]: row for row in challenges}
    anomalies = dict.fromkeys((key for key, _ in ANOMALIES), 0)

    # Users come from all three places a handle can appear, and every later table
    # resolves its handles through this map.
    posted_roles: dict[str, Counter[str]] = defaultdict(Counter)
    handles: set[str] = {row["author"] for row in challenges}
    for conversation in conversations:
        if conversation["assigned_to"]:
            handles.add(conversation["assigned_to"])
        for post in conversation["posts"]:
            handles.add(post["user"])
            posted_roles[post["user"]][post["user_role"]] += 1
    anomalies["multi_role_users"] = sum(1 for roles in posted_roles.values() if len(roles) > 1)

    _upsert(
        session,
        User.__table__,
        [
            {"handle": handle, "role": _role_for(posted_roles[handle])}
            for handle in sorted(handles)
        ],
        key=["handle"],
        update=["role"],
    )
    user_ids = _id_map(session, User.__table__, "handle")

    tag_names = {tag for row in challenges for tag in row["tags"]}
    tag_names |= {tag for row in conversations for tag in row["tags"]}
    # The name is the whole row apart from the id, so the update is a deliberate no-op:
    # DO NOTHING would be equivalent here, DO UPDATE is what the seed rules ask for.
    _upsert(
        session,
        Tag.__table__,
        [{"name": name} for name in sorted(tag_names)],
        key=["name"],
        update=["name"],
    )
    tag_ids = _id_map(session, Tag.__table__, "name")

    _upsert(
        session,
        Challenge.__table__,
        [
            {
                "external_id": row["challenge_id"],
                "title": row["title"],
                "description": row["description"],
                "category": row["category"],
                "difficulty": row["difficulty"],
                "points": _points(row["points"], row["challenge_id"], anomalies),
                "status": row["status"],
                "estimated_minutes": row["estimated_minutes"],
                "completion_rate": row["completion_rate"],
                "attempt_count": row["attempt_count"],
                "average_attempts_to_pass": row["average_attempts_to_pass"],
                "learning_objectives": row["learning_objectives"],
                "hints": row["hints"],
                "author_id": user_ids[row["author"]],
                "created_at": _ts(row["created_at"]),
                "updated_at": _ts(row["updated_at"]),
            }
            for row in challenges
        ],
        key=["external_id"],
        update=[
            "title", "description", "category", "difficulty", "points", "status",
            "estimated_minutes", "completion_rate", "attempt_count",
            "average_attempts_to_pass", "learning_objectives", "hints", "author_id",
            "created_at", "updated_at",
        ],
    )
    challenge_ids = _id_map(session, Challenge.__table__, "external_id")

    # Deduplicated: two rows with the same key in one ON CONFLICT DO UPDATE statement
    # is a Postgres error, not a no-op.
    tagged_challenges = sorted(
        {
            (challenge_ids[row["challenge_id"]], tag_ids[tag])
            for row in challenges
            for tag in row["tags"]
        }
    )
    _upsert(
        session,
        challenge_tags,
        [{"challenge_id": a, "tag_id": b} for a, b in tagged_challenges],
        key=["challenge_id", "tag_id"],
        update=["tag_id"],
    )

    prerequisites = sorted(
        {
            (challenge_ids[row["challenge_id"]], challenge_ids[prerequisite])
            for row in challenges
            for prerequisite in row["prerequisite_challenge_ids"]
        }
    )
    _upsert(
        session,
        challenge_prerequisites,
        [{"challenge_id": a, "prerequisite_id": b} for a, b in prerequisites],
        key=["challenge_id", "prerequisite_id"],
        update=["prerequisite_id"],
    )
    anomalies["unpublished_prerequisite"] = sum(
        1
        for row in challenges
        if row["status"] == "published"
        and any(
            challenge_by_external[prerequisite]["status"] in UNPUBLISHED
            for prerequisite in row["prerequisite_challenge_ids"]
        )
    )

    conversation_rows = []
    for row in conversations:
        # post_id is the order; the timestamps are not, which is what the anomaly
        # counters below are measuring.
        posts = sorted(row["posts"], key=lambda post: post["post_id"])
        times = [_ts(post["timestamp"]) for post in posts]
        created_at = _ts(row["created_at"])
        challenge = challenge_by_external[row["challenge_id"]]
        hours = row["resolution_time_hours"]
        terminal = row["status"] in enums.TERMINAL_STATUSES

        if any(later < earlier for earlier, later in zip(times, times[1:])):
            anomalies["non_monotonic_posts"] += 1
        if times[-1] < times[0]:
            anomalies["last_post_before_first"] += 1
        if hours is not None and created_at + timedelta(hours=hours) < max(times):
            anomalies["resolution_before_last_post"] += 1
        if challenge["status"] in UNPUBLISHED:
            anomalies["on_unpublished_challenge"] += 1
        if created_at < _ts(challenge["created_at"]):
            anomalies["before_challenge"] += 1

        conversation_rows.append(
            {
                "external_id": row["identifier"],
                "topic": row["topic"],
                "category": row["category"],
                "challenge_id": challenge_ids[row["challenge_id"]],
                "author_id": user_ids[posts[0]["user"]],
                "assignee_id": user_ids[row["assigned_to"]] if row["assigned_to"] else None,
                "status": row["status"],
                "priority": row["priority"],
                "is_pinned": row["is_pinned"],
                "is_locked": row["is_locked"],
                "view_count": row["view_count"],
                "created_at": created_at,
                "last_activity_at": _ts(row["last_activity_at"]),
                "resolved_at": (
                    created_at + timedelta(hours=hours) if terminal and hours is not None else None
                ),
            }
        )

    _insert_ignore(session, Conversation.__table__, conversation_rows, key=["external_id"])
    conversation_ids = _id_map(session, Conversation.__table__, "external_id")

    messages = []
    tagged = []
    for row in conversations:
        conversation_id = conversation_ids[row["identifier"]]
        for post in row["posts"]:
            messages.append(
                {
                    "conversation_id": conversation_id,
                    "sequence_no": post["post_id"],
                    "author_id": user_ids[post["user"]],
                    "posted_as_role": post["user_role"],
                    "body": post["content"],
                    "upvotes": post["reactions"]["upvotes"],
                    "helpful_count": post["reactions"]["helpful"],
                    "is_accepted": post["is_accepted_answer"],
                    "created_at": _ts(post["timestamp"]),
                }
            )
        tagged.extend(
            {"conversation_id": conversation_id, "tag_id": tag_ids[tag]}
            for tag in sorted(set(row["tags"]))
        )

    _insert_ignore(session, Message.__table__, messages, key=["conversation_id", "sequence_no"])
    _insert_ignore(session, conversation_tags, tagged, key=["conversation_id", "tag_id"])

    session.commit()

    report = SeedReport(skipped=False, counts=_row_counts(session), anomalies=anomalies)
    print("seed: row counts")
    for name, count in report.counts.items():
        print(f"  {name:<24} {count:>6}")
    print("seed: anomalies")
    for key, label in ANOMALIES:
        print(f"  {label:<56} {anomalies[key]:>6}")
    return report


def main() -> int:
    from app.db import SessionLocal

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    with SessionLocal() as session:
        seed(session, force=os.getenv("FORCE_SEED", "0") == "1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
