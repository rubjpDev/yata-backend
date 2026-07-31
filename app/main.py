"""FastAPI application entry point."""

from fastapi import FastAPI

from app import auth, blocks, bodyweight, exercises, health, sessions

app = FastAPI(
    title="YATA API",
    description="Powerlifting training analysis platform — REST API",
    version="0.1.0",
)

app.include_router(health.router, prefix="/v1")
app.include_router(auth.router, prefix="/v1")
app.include_router(exercises.router, prefix="/v1")
app.include_router(bodyweight.router, prefix="/v1")
app.include_router(blocks.router, prefix="/v1")
app.include_router(sessions.router, prefix="/v1")
