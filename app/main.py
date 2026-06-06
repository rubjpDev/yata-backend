"""FastAPI application entry point."""

from fastapi import FastAPI

from app.routers import health

app = FastAPI(
    title="YATA API",
    description="Powerlifting training analysis platform — REST API",
    version="0.1.0",
)

app.include_router(health.router, prefix="/v1")
