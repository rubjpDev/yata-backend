"""FastAPI application entry point."""

from fastapi import FastAPI

from app import auth, health

app = FastAPI(
    title="YATA API",
    description="Powerlifting training analysis platform — REST API",
    version="0.1.0",
)

app.include_router(health.router, prefix="/v1")
app.include_router(auth.router, prefix="/v1")
