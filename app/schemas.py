"""Pydantic request/response models.

Value sets come from enums.py so a schema cannot drift from a CHECK constraint.
Challenge and conversation `category` stay plain str on responses: categories are
the forum's taxonomy, not a constrained set (see enums.py); only POST
/conversations validates one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app import enums

MAX_BODY_CHARS = 20_000
MAX_TOPIC_CHARS = 300
MAX_SEARCH_CHARS = 200
HANDLE_PATTERN = r"^[a-z0-9_]{3,40}$"

ContentFormat = Literal["markdown"]
MARKDOWN: ContentFormat = "markdown"

UserRole = Literal[*enums.USER_ROLES]
ReactionType = Literal[*enums.REACTION_TYPES]
ChallengeStatus = Literal[*enums.CHALLENGE_STATUSES]
ChallengeDifficulty = Literal[*enums.CHALLENGE_DIFFICULTIES]
ConversationStatus = Literal[*enums.CONVERSATION_STATUSES]
ConversationPriority = Literal[*enums.CONVERSATION_PRIORITIES]
ConversationCategory = Literal[*enums.CONVERSATION_CATEGORIES]

T = TypeVar("T")


def _not_blank(value: str) -> str:
    """Trim, then insist something is left: a topic of spaces is not a topic."""
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("must not be blank")
    return trimmed


Topic = Annotated[str, Field(max_length=MAX_TOPIC_CHARS), AfterValidator(_not_blank)]
Body = Annotated[str, Field(max_length=MAX_BODY_CHARS), AfterValidator(_not_blank)]


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(BaseModel, Generic[T]):
    """Offset pagination. Stable for a fixed dataset; see README on keyset."""

    items: list[T]
    total: int
    limit: int
    offset: int


class UserOut(ResponseModel):
    id: int
    handle: str
    role: UserRole


class TagOut(ResponseModel):
    id: int
    name: str


class ChallengeRef(ResponseModel):
    """A prerequisite: enough to render and link it, with its own status."""

    id: int
    external_id: str
    title: str
    status: ChallengeStatus


class ChallengeSummary(ResponseModel):
    """The challenge as it appears on a conversation."""

    id: int
    external_id: str
    title: str
    category: str
    difficulty: ChallengeDifficulty
    status: ChallengeStatus


class ChallengeListItem(ResponseModel):
    id: int
    external_id: str
    title: str
    category: str
    difficulty: ChallengeDifficulty
    points: int
    status: ChallengeStatus
    estimated_minutes: int | None
    completion_rate: float | None
    attempt_count: int | None
    average_attempts_to_pass: float | None
    tags: list[TagOut]
    created_at: datetime
    updated_at: datetime
    content_format: ContentFormat = MARKDOWN


class ChallengeDetail(ChallengeListItem):
    description: str
    learning_objectives: list[str]
    hints: list[str]
    author: UserOut | None
    prerequisites: list[ChallengeRef]


class MessageOut(ResponseModel):
    id: int
    conversation_id: int
    sequence_no: int
    author: UserOut
    posted_as_role: UserRole
    body: str
    content_format: ContentFormat = MARKDOWN
    #: legacy imported aggregate + live message_reactions rows, never one or the other
    upvotes: int
    helpful_count: int
    #: None when X-User is absent, so "no reactions" and "no caller" stay distinct
    my_reactions: list[ReactionType] | None = None
    is_internal: bool
    is_accepted: bool
    is_spoiler: bool
    created_at: datetime
    deleted_at: datetime | None


class MessagePage(ResponseModel):
    """Keyset page over sequence_no: no total, because new replies never move earlier ones."""

    items: list[MessageOut]
    has_more: bool


class ConversationListItem(ResponseModel):
    id: int
    external_id: str
    topic: str
    category: str
    status: ConversationStatus
    priority: ConversationPriority
    is_pinned: bool
    is_locked: bool
    #: the imported snapshot; no endpoint increments it (see conversations_read)
    view_count: int
    challenge: ChallengeSummary
    author: UserOut
    assignee: UserOut | None
    tags: list[TagOut]
    #: over visible messages only
    message_count: int
    helpful_total: int
    has_accepted: bool
    created_at: datetime
    last_activity_at: datetime
    resolved_at: datetime | None
    deleted_at: datetime | None


class ConversationDetail(ConversationListItem):
    #: derived from the message carrying is_accepted; there is no column
    accepted_message_id: int | None
    messages: list[MessageOut]
    has_more: bool


class CreateUser(RequestModel):
    handle: str = Field(pattern=HANDLE_PATTERN)


class CreateConversation(RequestModel):
    challenge_id: int
    topic: Topic
    body: Body
    category: ConversationCategory
    priority: ConversationPriority = "medium"


class CreateMessage(RequestModel):
    body: Body
    is_internal: bool = False


class ReactionIn(RequestModel):
    type: ReactionType


class PatchConversation(RequestModel):
    """assignee_id: null unassigns, so routers read model_fields_set, not None."""

    status: ConversationStatus | None = None
    assignee_id: int | None = None
    priority: ConversationPriority | None = None
    is_pinned: bool | None = None
    is_locked: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_null_except_assignee(cls, data: Any) -> Any:
        """Absent means unchanged, so an explicit null is a client bug, not a value.

        assignee_id is the exception: its null is how a conversation is unassigned.
        """
        if not isinstance(data, dict):
            return data
        nulled = sorted(
            field
            for field, value in data.items()
            if value is None and field in cls.model_fields and field != "assignee_id"
        )
        if nulled:
            raise ValueError(f"null is not a value for: {', '.join(nulled)}")
        return data


class PatchMessage(RequestModel):
    is_spoiler: bool


class TagWithCounts(TagOut):
    """GET /tags. Both counts are visibility-filtered; see queries.tag_counts_query."""

    #: visible conversations only; soft-deleted ones never count, for any caller
    conversation_count: int
    #: published challenges only, for any caller
    challenge_count: int
