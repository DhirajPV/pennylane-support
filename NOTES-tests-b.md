# NOTES-tests-b (wave 3, test lane B)

Lane B owns `tests/test_concurrency.py`, `tests/test_insights.py`, `tests/test_ids.py`,
`tests/test_moderation.py` and one appended fixture in `tests/conftest.py`. No
application code was touched.

**No bug found.** Every test in the lane asserts what CLAUDE.md specifies and passes,
first run, against the real database. The two notes below are about the harness, not the
application.

## The connection pool is big enough for the concurrency test

`app/db.py` builds the engine with SQLAlchemy's defaults: `pool_size=5` plus
`max_overflow=10`, so fifteen connections. Ten concurrent replies fit, and no pool
setting had to change.

The overlap is real rather than incidental. A `TestClient` entered as a context manager
owns its own blocking portal, so ten clients are ten event loops; the fixture builds all
ten on the main thread (its `ExitStack` is not thread-safe) and hands one to each worker.
Instrumenting the test once with a start/end timestamp per request and sweeping the
events measured a peak of **10 of 10 requests in flight at the same instant**. The
instrumentation was removed; if the mechanism is ever changed, re-measure rather than
trust the green tick — a version of this test that serialises still passes.

## Trap: the two test lanes share one `pennylane_test` database

`docker-compose.yml` pins `name: pennylane-support` at the top level. A compose project
name from the file beats the directory name, so `docker compose run --rm app pytest` from
*any* worktree attaches to the same `pennylane-support-db-1` container and therefore to
the same `pennylane_test` database. Only the bind mount differs per worktree, which is
why each lane still collects its own tests.

That is fine one lane at a time and destructive with two: `conftest._reset_schema` runs
`DROP SCHEMA public CASCADE` at session start, and the `fresh_db` fixture runs it again
mid-session. A lane whose schema is dropped underneath it fails in ways that look like
application bugs. Both lanes' runs here were serialised by hand to avoid it.

The fix is one line, and it is outside this lane's file set:

```yaml
# docker-compose.yml
name: pennylane-support-${COMPOSE_SUFFIX:-main}
```

with each worktree exporting its own `COMPOSE_SUFFIX`. That gives every worktree its own
db container and volume. Until then, treat `docker compose run ... pytest` as needing a
lock across worktrees.

## Where the lane's tests deviate from nothing, and why one is expensive

`test_insights::test_seeded_insights` is the only test that takes `fresh_db`. Its numbers
are absolute — 1725 community replies, 84 unassigned — and by the time it runs, the other
tests in the session have posted replies and reactions of their own. It pays a drop, an
`alembic upgrade head` and a re-seed (~1.5s) to get the seeded totals back. Everything
else in the lane creates what it needs and asserts deltas or its own rows, so the suite
passes in file order, in reverse file order, and one file at a time.

`fresh_db` disposes the engine pool before dropping the schema: `DROP SCHEMA` blocks
behind any pooled connection still holding an open transaction.
