"""FastAPI application: routers and /health."""

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db import engine
from app.routers import routers

app = FastAPI(
    title="PennyLane support platform",
    description="Community support platform for PennyLane coding challenges.",
    version="0.1.0",
)

for router in routers:
    app.include_router(router)


@app.get("/health", tags=["meta"])
def health() -> JSONResponse:
    """Liveness plus a database round-trip."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "database": "unreachable", "detail": str(exc)},
        )

    return JSONResponse(content={"status": "ok", "database": "ok"})
