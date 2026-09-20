"""GET /insights: the support team's read-only dashboard, staff only.

Every number comes from one raw SQL statement in app.insights_sql; this module only binds
the parameters and shapes the result. Counts that a section reports per status or per
priority are zero-filled from enums.py, so an empty backlog still returns every key.
"""

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import insights_sql
from app.db import get_db
from app.deps import StaffUser
from app.enums import CONVERSATION_PRIORITIES, CONVERSATION_STATUSES, TERMINAL_STATUSES

router = APIRouter(tags=["insights"])

NON_TERMINAL_STATUSES = tuple(s for s in CONVERSATION_STATUSES if s not in TERMINAL_STATUSES)


def _number(value: Any) -> float | int | None:
    """Postgres numeric and double precision arrive as Decimal/float; JSON wants neither."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        value = float(value)
    return round(value, 2)


def _share(part: int, total: int) -> float | None:
    return round(part / total, 4) if total else None


def _split(community: int, staff: int) -> dict[str, Any]:
    total = community + staff
    return {
        "community": community,
        "staff": staff,
        "total": total,
        "community_share": _share(community, total),
    }


def _fill(counts: dict[str, int], keys: tuple[str, ...]) -> dict[str, int]:
    return {key: counts.get(key, 0) for key in keys}


def _community(db: Session) -> dict[str, Any]:
    row = db.execute(insights_sql.COMMUNITY, insights_sql.PARAMS).mappings().one()
    return {
        "accepted_answers": _split(row["accepted_community"], row["accepted_staff"]),
        "replies": _split(row["replies_community"], row["replies_staff"]),
        "first_replies": _split(row["first_replies_community"], row["first_replies_staff"]),
    }


def _top_contributors(db: Session) -> dict[str, Any]:
    rows = db.execute(insights_sql.TOP_CONTRIBUTORS, insights_sql.PARAMS).mappings().all()
    return {
        "by_accepted_answers": [
            {"id": r["id"], "handle": r["handle"], "role": r["role"],
             "accepted_answers": r["score"]}
            for r in rows
            if r["metric"] == "accepted_answers"
        ],
        "by_helpful_received": [
            {"id": r["id"], "handle": r["handle"], "role": r["role"],
             "helpful_received": r["score"]}
            for r in rows
            if r["metric"] == "helpful_received"
        ],
    }


def _knowledge_coverage(db: Session) -> dict[str, Any]:
    rows = db.execute(insights_sql.KNOWLEDGE_COVERAGE, insights_sql.PARAMS).mappings().all()
    without = [
        {"id": r["id"], "external_id": r["external_id"], "title": r["title"]}
        for r in rows
        if not r["has_accepted_answer"]
    ]
    return {
        "published_challenges": len(rows),
        "with_accepted_answer": len(rows) - len(without),
        "without_accepted_answer": len(without),
        "coverage": _share(len(rows) - len(without), len(rows)),
        "challenges_without_accepted_answer": without,
    }


def _challenge_load(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(insights_sql.CHALLENGE_LOAD, insights_sql.PARAMS).mappings().all()
    return [
        {
            "external_id": r["external_id"],
            "title": r["title"],
            "status": r["status"],
            "conversations": r["conversations"],
            "attempts": r["attempts"],
            "rate": _number(r["rate"]),
        }
        for r in rows
    ]


def _first_reply(db: Session) -> dict[str, Any]:
    row = db.execute(insights_sql.FIRST_REPLY, insights_sql.PARAMS).mappings().one()
    return {
        "median_hours": _number(row["median_hours"]),
        "p90_hours": _number(row["p90_hours"]),
        "sample_size": row["sample_size"],
        "excluded_negative": row["excluded_negative"],
        "no_reply": row["no_reply"],
    }


def _backlog(db: Session) -> dict[str, Any]:
    row = db.execute(insights_sql.BACKLOG, insights_sql.PARAMS).mappings().one()
    return {
        "by_status": _fill(row["by_status"], NON_TERMINAL_STATUSES),
        "by_priority": _fill(row["by_priority"], CONVERSATION_PRIORITIES),
        "unassigned": row["unassigned"],
        "urgent_unassigned": row["urgent_unassigned"],
    }


def _resolution(db: Session) -> dict[str, Any]:
    row = db.execute(insights_sql.RESOLUTION, insights_sql.PARAMS).mappings().one()
    return {
        "median_hours": _number(row["median_hours"]),
        "sample_size": row["sample_size"],
        "by_status": _fill(row["by_status"], tuple(sorted(TERMINAL_STATUSES))),
    }


def _data_quality(db: Session) -> dict[str, Any]:
    row = db.execute(insights_sql.DATA_QUALITY, insights_sql.PARAMS).mappings().one()
    return dict(row)


@router.get("/insights", summary="Support insights dashboard (staff only)")
def read_insights(
    db: Annotated[Session, Depends(get_db)],
    _staff: StaffUser,
) -> dict[str, Any]:
    return {
        "community": _community(db),
        "top_contributors": _top_contributors(db),
        "knowledge_coverage": _knowledge_coverage(db),
        "challenge_load": _challenge_load(db),
        "first_reply": _first_reply(db),
        "backlog": _backlog(db),
        "resolution": _resolution(db),
        "data_quality": _data_quality(db),
    }
