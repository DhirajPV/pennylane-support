# NOTES-read (wave 2, read lane)

The lane owns `app/routers/challenges.py`, `conversations_read.py` and `tags.py`. Those
three needed two additions outside the file set, authorised and made append-only. Nothing
else in either file was touched.

## Appended to `app/schemas.py`

`TagWithCounts(TagOut)` — `conversation_count`, `challenge_count`.

## Appended to `app/queries.py`

`tag_counts_query(db, user, *, limit, offset) -> tuple[list[TagWithCounts], int]`.

One grouped subquery per count, outer-joined onto `tags`, so unused tags stay in the list
at zero and neither count multiplies the other. Two statements per request: the `total`,
and the page.

The append carries its own `from app.schemas import TagWithCounts` partway down the file
rather than extending the import block at the top, which the "change nothing else"
constraint put out of reach. Fold it into the top block whenever schemas.py is next opened.

### Trap: the two counts do not use the same helper

CLAUDE.md "Visibility policy": *tag counts count only visible conversations and published
challenges.*

- conversations: `conversation_filter(user)`.
- challenges: `challenge_filter(None)` — **`None`, not `user`**. `challenge_filter(user)`
  returns `[]` for staff, which would put draft and archived challenges into a staff
  caller's `challenge_count`. Passing `None` asks the one visibility helper for the
  unprivileged predicate instead of re-deriving `status == 'published'` inline.

Verified: `arithmetic` carries exactly one `challenge_tags` row, on an unpublished
challenge, and reports `challenge_count: 0` to anonymous, learner and staff callers alike.

### `user` is inert today, deliberately

`tag_counts_query` takes no `include_deleted`, and `conversation_filter(user)` without it
appends `deleted_at IS NULL` whatever the role. So every caller gets identical counts right
now; `user` is there because the contract is visibility-filtered, and it is the seam if
`include_deleted` is ever plumbed through. `TagWithCounts`' field comments say counts are
caller-independent so nobody reads a permission difference into them.

## Ordering is collation-dependent, and that is fine

`conversation_count DESC, challenge_count DESC, name ASC`. Under the database's
`en_US.utf8` collation the hyphen is ignored at the primary level, so `parameters` sorts
*before* `parameter-shift` — the opposite of Python codepoint order. The order is still
total (88 distinct sort keys, no ties) and stable across requests, which is what
CLAUDE.md "Pagination, honestly" asks for. A test that asserts this order must use SQL as
its oracle, not `sorted()` in Python.

## Two router-level decisions worth knowing

- `page_messages` returns an empty page for a conversation it cannot see, so
  `conversations_read.py` does its own `conversation_filter` existence check first.
  Otherwise `GET /conversations/99999999/messages` would be `200 {"items": []}` instead of
  a 404. `GET /challenges/{id}/conversations` 404s on an invisible challenge for the same
  reason.
- `GET /conversations/{id}` increments `view_count` with `RETURNING`, so the body reports
  the count the read just stored rather than the one loaded a statement earlier.
