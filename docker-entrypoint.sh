#!/bin/sh
# Migrate, seed, then exec the command, so `docker compose up` and
# `docker compose run --rm app pytest` get the same prepared database.
set -e

echo "==> alembic upgrade head"
alembic upgrade head

if [ "${SKIP_SEED:-0}" = "1" ]; then
    echo "==> SKIP_SEED=1, skipping seed"
else
    echo "==> python -m app.seed.load"
    python -m app.seed.load
fi

if [ "${DEV:-0}" = "1" ] && [ "$1" = "uvicorn" ]; then
    echo "==> DEV=1, enabling --reload"
    exec "$@" --reload
fi

exec "$@"
