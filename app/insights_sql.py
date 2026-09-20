"""The raw SQL behind GET /insights: one statement per section of the response.

Two rules are repeated in every statement that touches them, because insights never
counts hidden rows: conversations.deleted_at IS NULL, and on messages
deleted_at IS NULL AND is_internal = false. Internal messages are invisible to the
asker, so they are neither replies nor answers.

PARAMS carries the two value sets the statements compare against. They are bound, not
spelled out, so TERMINAL_STATUSES and STAFF_ROLES stay defined in one place. Passing the
whole dict to a statement that uses neither is harmless: SQLAlchemy binds only the
parameters the statement names.
"""

from typing import Any

from sqlalchemy import text

from app.enums import STAFF_ROLES, TERMINAL_STATUSES

PARAMS: dict[str, Any] = {
    "staff_roles": sorted(STAFF_ROLES),
    "terminal_statuses": sorted(TERMINAL_STATUSES),
}

# Community vs staff. Provenance is the author's CURRENT users.role, not posted_as_role.
# first_replies uses the same definition as the first_reply section below -- earliest
# visible message by someone other than the asker -- so the two sections cannot disagree
# about which message was the first reply, or about how many conversations have one.
COMMUNITY = text(
    """
    WITH visible AS (
        SELECT m.conversation_id,
               m.sequence_no,
               m.is_accepted,
               m.author_id <> c.author_id AS by_other,
               u.role = ANY(:staff_roles) AS by_staff
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        JOIN users u ON u.id = m.author_id
        WHERE m.deleted_at IS NULL
          AND m.is_internal = false
          AND c.deleted_at IS NULL
    ),
    first_replies AS (
        SELECT DISTINCT ON (conversation_id) conversation_id, by_staff
        FROM visible
        WHERE by_other
        ORDER BY conversation_id, sequence_no
    )
    SELECT
        count(*) FILTER (WHERE is_accepted AND NOT by_staff) AS accepted_community,
        count(*) FILTER (WHERE is_accepted AND by_staff) AS accepted_staff,
        count(*) FILTER (WHERE sequence_no > 1 AND NOT by_staff) AS replies_community,
        count(*) FILTER (WHERE sequence_no > 1 AND by_staff) AS replies_staff,
        (SELECT count(*) FROM first_replies WHERE NOT by_staff) AS first_replies_community,
        (SELECT count(*) FROM first_replies WHERE by_staff) AS first_replies_staff
    FROM visible
    """
)

# Both leaderboards in one statement, tagged by metric. helpful is the same legacy + live
# sum the API reports on a message: messages.helpful_count plus live 'helpful' reactions.
TOP_CONTRIBUTORS = text(
    """
    WITH visible AS (
        SELECT m.id, m.author_id, m.is_accepted, m.helpful_count
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.deleted_at IS NULL
          AND m.is_internal = false
          AND c.deleted_at IS NULL
    ),
    live_helpful AS (
        SELECT message_id, count(*) AS reactions
        FROM message_reactions
        WHERE type = 'helpful'
        GROUP BY message_id
    ),
    accepted AS (
        SELECT author_id, count(*) AS score
        FROM visible
        WHERE is_accepted
        GROUP BY author_id
        ORDER BY score DESC, author_id
        LIMIT 10
    ),
    helpful AS (
        -- ::bigint because sum() over a bigint is numeric, and the union's other
        -- branch is a count: without it both scores come back as Decimal.
        SELECT v.author_id,
               sum(v.helpful_count + coalesce(l.reactions, 0))::bigint AS score
        FROM visible v
        LEFT JOIN live_helpful l ON l.message_id = v.id
        GROUP BY v.author_id
        HAVING sum(v.helpful_count + coalesce(l.reactions, 0)) > 0
        ORDER BY score DESC, v.author_id
        LIMIT 10
    )
    SELECT 'accepted_answers' AS metric, u.id, u.handle, u.role, accepted.score
    FROM accepted
    JOIN users u ON u.id = accepted.author_id
    UNION ALL
    SELECT 'helpful_received' AS metric, u.id, u.handle, u.role, helpful.score
    FROM helpful
    JOIN users u ON u.id = helpful.author_id
    ORDER BY metric, score DESC, id
    """
)

# One row per published challenge; the endpoint splits it into the two counts and the
# list of challenges still waiting for an accepted answer.
KNOWLEDGE_COVERAGE = text(
    """
    WITH covered AS (
        SELECT DISTINCT c.challenge_id
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.is_accepted
          AND m.deleted_at IS NULL
          AND m.is_internal = false
          AND c.deleted_at IS NULL
    )
    SELECT ch.id,
           ch.external_id,
           ch.title,
           covered.challenge_id IS NOT NULL AS has_accepted_answer
    FROM challenges ch
    LEFT JOIN covered ON covered.challenge_id = ch.id
    WHERE ch.status = 'published'
    ORDER BY ch.external_id
    """
)

# Which challenges generate support load relative to how much they are attempted.
# NULLIF(attempt_count, 0) makes a challenge with no recorded attempts a NULL rate
# rather than a division error; NULLS LAST keeps those out of the top 10.
CHALLENGE_LOAD = text(
    """
    WITH per_challenge AS (
        SELECT ch.external_id,
               ch.title,
               ch.status,
               ch.attempt_count,
               count(*) AS conversations
        FROM challenges ch
        JOIN conversations c ON c.challenge_id = ch.id AND c.deleted_at IS NULL
        GROUP BY ch.id
    )
    SELECT external_id,
           title,
           status,
           conversations,
           attempt_count AS attempts,
           round(conversations * 1000.0 / NULLIF(attempt_count, 0), 2) AS rate
    FROM per_challenge
    ORDER BY rate DESC NULLS LAST, conversations DESC, external_id
    LIMIT 10
    """
)

# Time to the first visible reply from someone other than the asker. DISTINCT ON takes it
# by sequence_no, never created_at: the imported timestamps are disordered, which is also
# why a delta can come out negative. Those are counted, not silently folded into the median.
FIRST_REPLY = text(
    """
    WITH candidates AS (
        SELECT DISTINCT ON (c.id)
               c.id AS conversation_id,
               EXTRACT(EPOCH FROM (m.created_at - c.created_at)) / 3600.0 AS hours
        FROM conversations c
        JOIN messages m ON m.conversation_id = c.id
        WHERE c.deleted_at IS NULL
          AND m.deleted_at IS NULL
          AND m.is_internal = false
          AND m.author_id <> c.author_id
        ORDER BY c.id, m.sequence_no
    )
    SELECT
        percentile_cont(0.5) WITHIN GROUP (ORDER BY hours)
            FILTER (WHERE hours >= 0) AS median_hours,
        percentile_cont(0.9) WITHIN GROUP (ORDER BY hours)
            FILTER (WHERE hours >= 0) AS p90_hours,
        count(*) FILTER (WHERE hours >= 0) AS sample_size,
        count(*) FILTER (WHERE hours < 0) AS excluded_negative,
        (SELECT count(*) FROM conversations WHERE deleted_at IS NULL)
            - count(*) AS no_reply
    FROM candidates
    """
)

# What is still on the queue: everything not in a terminal status.
BACKLOG = text(
    """
    WITH backlog AS (
        SELECT status, priority, assignee_id
        FROM conversations
        WHERE deleted_at IS NULL
          AND status <> ALL(:terminal_statuses)
    )
    SELECT
        coalesce(
            (SELECT jsonb_object_agg(status, n)
             FROM (SELECT status, count(*) AS n FROM backlog GROUP BY status) s),
            '{}'::jsonb
        ) AS by_status,
        coalesce(
            (SELECT jsonb_object_agg(priority, n)
             FROM (SELECT priority, count(*) AS n FROM backlog GROUP BY priority) p),
            '{}'::jsonb
        ) AS by_priority,
        (SELECT count(*) FROM backlog WHERE assignee_id IS NULL) AS unassigned,
        (SELECT count(*) FROM backlog
          WHERE assignee_id IS NULL AND priority = 'urgent') AS urgent_unassigned
    """
)

# resolved_at is the LATEST resolution: a reopen clears it and resolving again sets a new
# one, so this measures the resolution that stuck, not the first one.
RESOLUTION = text(
    """
    WITH terminal AS (
        SELECT status, created_at, resolved_at
        FROM conversations
        WHERE deleted_at IS NULL
          AND status = ANY(:terminal_statuses)
    ),
    timed AS (
        SELECT EXTRACT(EPOCH FROM (resolved_at - created_at)) / 3600.0 AS hours
        FROM terminal
        WHERE resolved_at IS NOT NULL
    )
    SELECT
        (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY hours) FROM timed) AS median_hours,
        (SELECT count(*) FROM timed) AS sample_size,
        coalesce(
            (SELECT jsonb_object_agg(status, n)
             FROM (SELECT status, count(*) AS n FROM terminal GROUP BY status) s),
            '{}'::jsonb
        ) AS by_status
    """
)

# The same anomalies the seed reports, recounted live: what the imported data says that
# the lifecycle rules would not have allowed.
DATA_QUALITY = text(
    """
    SELECT
        (SELECT count(*)
           FROM conversations c
           JOIN challenges ch ON ch.id = c.challenge_id
          WHERE c.deleted_at IS NULL
            AND ch.status <> 'published') AS conversations_on_unpublished_challenges,
        (SELECT count(*)
           FROM conversations c
           JOIN challenges ch ON ch.id = c.challenge_id
          WHERE c.deleted_at IS NULL
            AND c.created_at < ch.created_at) AS conversations_created_before_challenge,
        (SELECT count(DISTINCT cp.challenge_id)
           FROM challenge_prerequisites cp
           JOIN challenges ch ON ch.id = cp.challenge_id
           JOIN challenges pre ON pre.id = cp.prerequisite_id
          WHERE ch.status = 'published'
            AND pre.status <> 'published') AS published_with_unpublished_prerequisite,
        (SELECT count(*)
           FROM conversations
          WHERE deleted_at IS NULL
            AND last_activity_at < created_at) AS conversations_with_activity_before_creation
    """
)
