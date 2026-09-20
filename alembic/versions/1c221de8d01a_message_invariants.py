"""message invariants

Four rules the API already enforces, written into the schema so a bad row cannot arrive
by any other route: a bulk seed, a fixup by hand, or a future endpoint that forgets one.
The seeded data satisfies all four, so this revision applies to a loaded database.

Revision ID: 1c221de8d01a
Revises: 6580d5f52bed
Create Date: 2026-09-20 19:49:54.716866

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '1c221de8d01a'
down_revision: Union[str, Sequence[str], None] = '6580d5f52bed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINTS = (
    ("ck_messages_sequence_no_positive", "sequence_no >= 1"),
    ("ck_messages_counters_non_negative", "upvotes >= 0 AND helpful_count >= 0"),
    ("ck_messages_accepted_not_first", "NOT (is_accepted AND sequence_no = 1)"),
    ("ck_messages_accepted_not_internal", "NOT (is_accepted AND is_internal)"),
)


def upgrade() -> None:
    """Upgrade schema."""
    for name, condition in CONSTRAINTS:
        op.create_check_constraint(name, "messages", condition)


def downgrade() -> None:
    """Downgrade schema."""
    for name, _ in reversed(CONSTRAINTS):
        op.drop_constraint(name, "messages", type_="check")
