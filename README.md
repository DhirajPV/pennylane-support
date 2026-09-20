# PennyLane support platform

A community-driven support platform for PennyLane coding challenges: learners ask for
help on a challenge, anyone can reply, and the asker or the support team marks the
accepted answer. The support team's workflow sits on top of that as a moderation layer.

Built as a take-home. Python 3.12, FastAPI, SQLAlchemy 2.0 (sync), Alembic, Postgres 18.

## Setup

The only prerequisite is Docker. Nothing else needs to be installed, and no `.env`
file is required: `docker-compose.yml` sets every default inline.

```sh
docker compose up --build
```

That builds the image, starts Postgres, waits for it to pass its healthcheck, runs
`alembic upgrade head`, runs the seed, and starts the API:

- API docs: http://localhost:8000/docs
- Health:   http://localhost:8000/health — returns `{"status": "ok", "database": "ok"}`
  only when the app can actually reach Postgres.

Port 8000 is configurable if it is taken: `APP_PORT=8080 docker compose up --build`.
Postgres itself is not published to the host, so it cannot collide with a local one.
Postgres 18 stores data under `/var/lib/postgresql`; the volume mounts there. `make reset` wipes it.

### Make targets

```sh
make up        # build and start the stack
make down      # stop it, keep the data
make reset     # stop it and delete the database volume
make migrate   # alembic upgrade head against the running stack
make seed      # re-run the seed against the running stack
make test      # pytest in a one-off container, after migrations and seed
make logs      # follow the app logs
make psql      # psql shell on the database
```

### Without make

Every target is a single compose command:

```sh
docker compose up --build                              # make up
docker compose down                                    # make down
docker compose down -v                                 # make reset
docker compose exec app alembic upgrade head           # make migrate
docker compose exec app python -m app.seed.load        # make seed
docker compose run --rm app pytest                     # make test
docker compose logs -f app                             # make logs
docker compose exec db psql -U pennylane -d pennylane  # make psql
```

The image's entrypoint migrates and seeds before exec'ing whatever command it is
given, which is why `docker compose run --rm app pytest` runs tests against a
migrated, seeded database rather than starting the server. `SKIP_SEED=1` skips the
seed step; `DEV=1` (set by compose) starts uvicorn with `--reload`.

### Outside Docker

Copy `.env.example` to `.env`, point `DATABASE_URL` at a Postgres 18 you provide, then:

```sh
pip install -r requirements.lock && pip install --no-deps -e .
alembic upgrade head
uvicorn app.main:app --reload
```
