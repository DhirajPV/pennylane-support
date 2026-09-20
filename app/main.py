"""FastAPI application: routers and /health."""

import logging

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db import engine
from app.routers import routers

logger = logging.getLogger(__name__)

app = FastAPI(
    title="PennyLane support platform",
    description="Community support platform for PennyLane coding challenges.",
    version="0.1.0",
)

for router in routers:
    app.include_router(router)


@app.get("/health", tags=["meta"])
def health() -> JSONResponse:
    """Liveness plus a database round-trip.

    The failure body is fixed text. A driver exception carries the host, port, user
    and often the database name of whatever it failed to reach, and /health is the one
    endpoint that is unauthenticated by design; the detail goes to the log instead.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health check failed: database unreachable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "database": "unavailable"},
        )

    return JSONResponse(content={"status": "ok", "database": "ok"})
