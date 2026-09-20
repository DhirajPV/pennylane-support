# Notes from the seed lane

Neither item blocks the seed: under `docker compose` (the only documented way to run
this) both work as written today. Both are gaps against CLAUDE.md, in files this lane
does not own.

## 1. `make seed` does not honour FORCE_SEED

CLAUDE.md: "`make seed` runs the seed directly (honours FORCE_SEED, not SKIP_SEED)".

The target is `docker compose exec app python -m app.seed.load`. `exec` does not forward
the host environment, so `FORCE_SEED=1 make seed` reaches the container unset and the
seed takes its skip path:

    $ FORCE_SEED=1 docker compose exec -T app sh -c 'echo ${FORCE_SEED:-<unset>}'
    <unset>

Wanted, in `Makefile`:

    seed:     ## Run the seed against the running stack
    	$(COMPOSE) exec -e FORCE_SEED=$(FORCE_SEED) app python -m app.seed.load

(`-e FORCE_SEED` alone also works and forwards only when set.)

## 2. `data/` is not in the image

The Dockerfile copies `app`, `alembic` and `alembic.ini`, but not `data/`. The seed reads
`data/*.json` and only finds them because compose bind-mounts `.:/app` over the image:

    $ docker run --rm --entrypoint sh pennylane-support-app -c 'ls /app/data'
    ls: cannot access '/app/data': No such file or directory

So the image cannot run its own ENTRYPOINT seed without the bind mount. Wanted, in
`Dockerfile`, next to the other COPY lines:

    COPY data ./data
