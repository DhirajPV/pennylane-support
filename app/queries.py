"""Every read the API serves: eager-loaded, visibility-filtered, synchronous.

Relationships are lazy="raise", so whatever a response model touches is loaded
here. Reaction totals are always the legacy imported aggregate plus live
message_reactions rows (CLAUDE.md "Reactions"); no read path computes them twice
over.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, literal, select
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.sql.elements import ColumnElement

from app import enums
from app.models import (
    Challenge,
    Conversation,
    Message,
    MessageReaction,
    Tag,
    User,
    challenge_tags,
    conversation_tags,
)
from app.schemas import (
    ChallengeDetail,
    ChallengeListItem,
    ChallengeSummary,
    ConversationDetail,
    ConversationListItem,
    MessageOut,
    MessagePage,
    TagOut,
    UserOut,
)
from app.visibility import (
    challenge_filter,
    conversation_filter,
    mask_message,
    message_filter,
)

DETAIL_MESSAGE_LIMIT = 50
CONVERSATION_SORTS = ("activity", "newest", "helpful", "priority")

UPVOTE, HELPFUL = enums.REACTION_TYPES

_LIKE_ESCAPE = "\\"

#: enums order is low -> urgent, so descending rank is the queue order.
_PRIORITY_RANK = case(
    {priority: rank for rank, priority in enumerate(enums.CONVERSATION_PRIORITIES)},
    value=Conversation.priority,
    else_=-1,
)


def _contains(column: ColumnElement[str], term: str) -> ColumnElement[bool]:
    """ILIKE %term%, with the caller's own % and _ neutralised."""
    pattern = term
    for char in (_LIKE_ESCAPE, "%", "_"):
        pattern = pattern.replace(char, _LIKE_ESCAPE + char)
    return column.ilike(f"%{pattern}%", escape=_LIKE_ESCAPE)


def _reaction_rollup(
    db: Session, message_ids: list[int], user: User | None
) -> dict[tuple[int, str], tuple[int, bool]]:
    """(message_id, type) -> (live count, whether `user` is one of them), in one query."""
    if not message_ids:
        return {}
    mine = func.bool_or(MessageReaction.user_id == user.id) if user else literal(False)
    rows = db.execute(
        select(
            MessageReaction.message_id,
            MessageReaction.type,
            func.count().label("live"),
            mine.label("mine"),
        )
        .where(MessageReaction.message_id.in_(message_ids))
        .group_by(MessageReaction.message_id, MessageReaction.type)
    ).all()
    return {(row.message_id, row.type): (row.live, bool(row.mine)) for row in rows}


def _message_out(
    message: Message,
    rollup: dict[tuple[int, str], tuple[int, bool]],
    user: User | None,
    reveal_spoilers: bool,
) -> MessageOut:
    live_upvotes, mine_upvote = rollup.get((message.id, UPVOTE), (0, False))
    live_helpful, mine_helpful = rollup.get((message.id, HELPFUL), (0, False))
    my_reactions = None
    if user is not None:
        my_reactions = [
            reaction
            for reaction, mine in ((UPVOTE, mine_upvote), (HELPFUL, mine_helpful))
            if mine
        ]
    return MessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        sequence_no=message.sequence_no,
        author=UserOut.model_validate(message.author),
        posted_as_role=message.posted_as_role,
        body=mask_message(message, user, reveal_spoilers),
        upvotes=message.upvotes + live_upvotes,
        helpful_count=message.helpful_count + live_helpful,
        my_reactions=my_reactions,
        is_internal=message.is_internal,
        is_accepted=message.is_accepted,
        is_spoiler=message.is_spoiler,
        created_at=message.created_at,
        deleted_at=message.deleted_at,
    )


def _conversation_columns(conversation: Conversation) -> dict[str, Any]:
    """The fields both conversation shapes share; the caller adds the derived counts."""
    return {
        "id": conversation.id,
        "external_id": conversation.external_id,
        "topic": conversation.topic,
        "category": conversation.category,
        "status": conversation.status,
        "priority": conversation.priority,
        "is_pinned": conversation.is_pinned,
        "is_locked": conversation.is_locked,
        "view_count": conversation.view_count,
        "challenge": ChallengeSummary.model_validate(conversation.challenge),
        "author": UserOut.model_validate(conversation.author),
        "assignee": (
            UserOut.model_validate(conversation.assignee) if conversation.assignee else None
        ),
        "tags": [TagOut.model_validate(tag) for tag in conversation.tags],
        "created_at": conversation.created_at,
        "last_activity_at": conversation.last_activity_at,
        "resolved_at": conversation.resolved_at,
        "deleted_at": conversation.deleted_at,
    }


def _conversation_eager_loads() -> tuple[Any, ...]:
    return (
        joinedload(Conversation.challenge),
        joinedload(Conversation.author),
        joinedload(Conversation.assignee),
        selectinload(Conversation.tags),
    )


def load_conversation_detail(
    db: Session,
    conversation_id: int,
    user: User | None,
    *,
    include_deleted: bool = False,
    reveal_spoilers: bool = False,
) -> ConversationDetail | None:
    """The detail shape, or None when the conversation is not visible to `user`.

    Does not touch view_count: the router increments it, because a read helper
    used by mutations must stay side-effect free.
    """
    conversation = db.scalars(
        select(Conversation)
        .options(*_conversation_eager_loads())
        .where(
            Conversation.id == conversation_id,
            *conversation_filter(user, include_deleted=include_deleted),
        )
    ).one_or_none()
    if conversation is None:
        return None

    scope = (
        Message.conversation_id == conversation_id,
        *message_filter(user, reveal_spoilers=reveal_spoilers, include_deleted=include_deleted),
    )
    live_helpful = (
        select(func.count())
        .select_from(MessageReaction)
        .where(
            MessageReaction.type == HELPFUL,
            MessageReaction.message_id.in_(select(Message.id).where(*scope)),
        )
        .scalar_subquery()
    )
    message_count, helpful_total, accepted_message_id = db.execute(
        select(
            func.count(Message.id),
            func.coalesce(func.sum(Message.helpful_count), 0) + live_helpful,
            func.max(case((Message.is_accepted, Message.id))),
        ).where(*scope)
    ).one()

    messages = db.scalars(
        select(Message)
        .options(joinedload(Message.author))
        .where(*scope)
        .order_by(Message.sequence_no)
        .limit(DETAIL_MESSAGE_LIMIT)
    ).all()
    rollup = _reaction_rollup(db, [message.id for message in messages], user)

    return ConversationDetail(
        **_conversation_columns(conversation),
        message_count=message_count,
        helpful_total=helpful_total,
        has_accepted=accepted_message_id is not None,
        accepted_message_id=accepted_message_id,
        has_more=message_count > len(messages),
        messages=[
            _message_out(message, rollup, user, reveal_spoilers) for message in messages
        ],
    )


def _message_stats(user: User | None, *, include_deleted: bool):
    """Per-conversation aggregates over the messages `user` can see.

    Live helpful reactions are collapsed per message before the join, so the join
    back to messages stays 1:0..1 and sum(helpful_count) is not multiplied.
    """
    live_helpful = (
        select(
            MessageReaction.message_id.label("message_id"),
            func.count().label("live"),
        )
        .where(MessageReaction.type == HELPFUL)
        .group_by(MessageReaction.message_id)
        .subquery()
    )
    return (
        select(
            Message.conversation_id.label("conversation_id"),
            func.count(Message.id).label("message_count"),
            (
                func.coalesce(func.sum(Message.helpful_count), 0)
                + func.coalesce(func.sum(live_helpful.c.live), 0)
            ).label("helpful_total"),
            func.bool_or(Message.is_accepted).label("has_accepted"),
        )
        .select_from(Message)
        .outerjoin(live_helpful, live_helpful.c.message_id == Message.id)
        .where(*message_filter(user, include_deleted=include_deleted))
        .group_by(Message.conversation_id)
        .subquery()
    )


def conversation_list_query(
    db: Session,
    user: User | None,
    *,
    filters: dict[str, Any],
    sort: str,
    limit: int,
    offset: int,
    include_deleted: bool = False,
) -> tuple[list[ConversationListItem], int]:
    """A page of conversations plus the total for that filter set.

    filters: q, status, priority, assignee (handle), challenge_id, tag (name),
    has_accepted, unassigned. Rejecting unassigned together with assignee is the
    router's 422, not this function's.
    """
    stats = _message_stats(user, include_deleted=include_deleted)
    conditions: list[ColumnElement[bool]] = [
        *conversation_filter(user, include_deleted=include_deleted)
    ]

    if term := filters.get("q"):
        conditions.append(_contains(Conversation.topic, term))
    if status := filters.get("status"):
        conditions.append(Conversation.status == status)
    if priority := filters.get("priority"):
        conditions.append(Conversation.priority == priority)
    if handle := filters.get("assignee"):
        # An unknown handle matches nothing rather than erroring.
        conditions.append(
            Conversation.assignee_id
            == select(User.id).where(User.handle == handle).scalar_subquery()
        )
    if (challenge_id := filters.get("challenge_id")) is not None:
        conditions.append(Conversation.challenge_id == challenge_id)
    if tag := filters.get("tag"):
        conditions.append(
            select(conversation_tags.c.conversation_id)
            .join(Tag, Tag.id == conversation_tags.c.tag_id)
            .where(
                conversation_tags.c.conversation_id == Conversation.id,
                Tag.name == tag,
            )
            .exists()
        )
    if (has_accepted := filters.get("has_accepted")) is not None:
        conditions.append(func.coalesce(stats.c.has_accepted, False).is_(bool(has_accepted)))
    if filters.get("unassigned"):
        conditions.append(Conversation.assignee_id.is_(None))

    pinned_first = Conversation.is_pinned.desc()
    if sort == "helpful":
        order = (pinned_first, func.coalesce(stats.c.helpful_total, 0).desc())
    elif sort == "newest":
        order = (pinned_first, Conversation.created_at.desc())
    elif sort == "priority":
        # The queue sort ignores pinning: pinning is a browse signal.
        order = (_PRIORITY_RANK.desc(), Conversation.last_activity_at.desc())
    else:
        order = (pinned_first, Conversation.last_activity_at.desc())

    total = db.scalar(
        select(func.count())
        .select_from(Conversation)
        .outerjoin(stats, stats.c.conversation_id == Conversation.id)
        .where(*conditions)
    )
    rows = db.execute(
        select(
            Conversation,
            func.coalesce(stats.c.message_count, 0),
            func.coalesce(stats.c.helpful_total, 0),
            func.coalesce(stats.c.has_accepted, False),
        )
        .options(*_conversation_eager_loads())
        .outerjoin(stats, stats.c.conversation_id == Conversation.id)
        .where(*conditions)
        .order_by(*order, Conversation.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    items = [
        ConversationListItem(
            **_conversation_columns(conversation),
            message_count=message_count,
            helpful_total=helpful_total,
            has_accepted=accepted,
        )
        for conversation, message_count, helpful_total, accepted in rows
    ]
    return items, total or 0


def page_messages(
    db: Session,
    conversation_id: int,
    user: User | None,
    *,
    after_sequence: int | None,
    limit: int,
    reveal_spoilers: bool = False,
    include_deleted: bool = False,
) -> MessagePage:
    """Keyset page over sequence_no. Empty when the conversation is not visible."""
    conversation_visible = (
        select(Conversation.id)
        .where(
            Conversation.id == conversation_id,
            *conversation_filter(user, include_deleted=include_deleted),
        )
        .exists()
    )
    stmt = (
        select(Message)
        .options(joinedload(Message.author))
        .where(
            Message.conversation_id == conversation_id,
            conversation_visible,
            *message_filter(
                user, reveal_spoilers=reveal_spoilers, include_deleted=include_deleted
            ),
        )
        .order_by(Message.sequence_no)
        .limit(limit + 1)
    )
    if after_sequence is not None:
        stmt = stmt.where(Message.sequence_no > after_sequence)

    rows = list(db.scalars(stmt).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    rollup = _reaction_rollup(db, [message.id for message in rows], user)
    return MessagePage(
        items=[_message_out(message, rollup, user, reveal_spoilers) for message in rows],
        has_more=has_more,
    )


def challenge_list_query(
    db: Session,
    user: User | None,
    *,
    filters: dict[str, Any],
    limit: int,
    offset: int,
) -> tuple[list[ChallengeListItem], int]:
    """A page of challenges plus the total. filters: category, difficulty, tag, status.

    Non-staff never see past challenge_filter, so a non-staff status=draft is an
    empty page rather than a leak.
    """
    conditions: list[ColumnElement[bool]] = [*challenge_filter(user)]
    if category := filters.get("category"):
        conditions.append(Challenge.category == category)
    if difficulty := filters.get("difficulty"):
        conditions.append(Challenge.difficulty == difficulty)
    if status := filters.get("status"):
        conditions.append(Challenge.status == status)
    if tag := filters.get("tag"):
        conditions.append(
            select(challenge_tags.c.challenge_id)
            .join(Tag, Tag.id == challenge_tags.c.tag_id)
            .where(challenge_tags.c.challenge_id == Challenge.id, Tag.name == tag)
            .exists()
        )

    total = db.scalar(select(func.count()).select_from(Challenge).where(*conditions))
    rows = db.scalars(
        select(Challenge)
        .options(selectinload(Challenge.tags))
        .where(*conditions)
        .order_by(Challenge.created_at.desc(), Challenge.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [ChallengeListItem.model_validate(row) for row in rows], total or 0


def load_challenge(db: Session, id: int, user: User | None) -> ChallengeDetail | None:
    """The detail shape, or None when not published and `user` is not staff (the router's 404).

    Prerequisites are listed whatever their status: a published challenge gated
    behind a draft one is a data-quality signal the reader needs to see.
    """
    challenge = db.scalars(
        select(Challenge)
        .options(
            joinedload(Challenge.author),
            selectinload(Challenge.tags),
            selectinload(Challenge.prerequisites),
        )
        .where(Challenge.id == id, *challenge_filter(user))
    ).one_or_none()
    if challenge is None:
        return None
    return ChallengeDetail.model_validate(challenge)


from app.schemas import TagWithCounts


def tag_counts_query(
    db: Session,
    user: User | None,
    *,
    limit: int,
    offset: int,
) -> tuple[list[TagWithCounts], int]:
    """Every tag with its visible usage. Unused tags stay, at zero.

    One grouped subquery per count, outer-joined onto tags: the counts are
    independent, so joining the two link tables in one pass would multiply them.
    """
    conversation_counts = (
        select(conversation_tags.c.tag_id.label("tag_id"), func.count().label("n"))
        .join(Conversation, Conversation.id == conversation_tags.c.conversation_id)
        .where(*conversation_filter(user))
        .group_by(conversation_tags.c.tag_id)
        .subquery()
    )
    challenge_counts = (
        select(challenge_tags.c.tag_id.label("tag_id"), func.count().label("n"))
        .join(Challenge, Challenge.id == challenge_tags.c.challenge_id)
        # challenge_filter(None), not (user): the challenge side is published-only for
        # every caller, so a staff reader must not find drafts in a tag count.
        .where(*challenge_filter(None))
        .group_by(challenge_tags.c.tag_id)
        .subquery()
    )
    conversation_count = func.coalesce(conversation_counts.c.n, 0)
    challenge_count = func.coalesce(challenge_counts.c.n, 0)

    total = db.scalar(select(func.count()).select_from(Tag))
    rows = db.execute(
        select(Tag.id, Tag.name, conversation_count, challenge_count)
        .outerjoin(conversation_counts, conversation_counts.c.tag_id == Tag.id)
        .outerjoin(challenge_counts, challenge_counts.c.tag_id == Tag.id)
        .order_by(conversation_count.desc(), challenge_count.desc(), Tag.name)
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        TagWithCounts(
            id=id,
            name=name,
            conversation_count=conversations,
            challenge_count=challenges,
        )
        for id, name, conversations, challenges in rows
    ], total or 0
