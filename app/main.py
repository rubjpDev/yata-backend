"""FastAPI application entry point."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app import auth, blocks, bodyweight, coach, exercises, health, profile, sessions
from app.config import settings
from app.deps import get_embedding_client, get_llm_client

logger = logging.getLogger(__name__)


async def _warm_embedding_client() -> None:
    """Load the ~130 MB embedding model off the event loop, best-effort.

    A failed warm-up is only an optimisation lost, not an outage: it is
    logged and swallowed so it can never crash the app or fail the
    healthcheck (R41). `get_embedding_client` is `lru_cache`d, so this and the
    first real request share one instance.
    """
    try:
        await asyncio.to_thread(get_embedding_client)
    except Exception:  # noqa: BLE001 - a warm-up must never propagate
        logger.exception("embedding client warm-up failed; serving without it")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Warm the embedding client in the background, production only (R39-R41).

    Scheduled with `create_task` rather than awaited, so startup — and
    `GET /v1/health` — never wait on the model to load (D-6).

    Also builds the LLM client eagerly, but only when `YATA_LLM=bedrock`
    (yata-0018): unlike the embedding warm-up, this call is unguarded on
    purpose — if the real client is requested and no AWS credentials are
    found, `get_llm_client()` raises and startup fails loudly, rather than
    letting the process serve traffic that would fail mid-request.
    """
    if settings.environment == "production":
        asyncio.create_task(_warm_embedding_client())
    if settings.llm_provider == "bedrock":
        get_llm_client()
    yield


app = FastAPI(
    title="YATA API",
    description="Powerlifting training analysis platform — REST API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,  # tokens travel in Authorization, never a cookie
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(health.router, prefix="/v1")
app.include_router(auth.router, prefix="/v1")
app.include_router(exercises.router, prefix="/v1")
app.include_router(bodyweight.router, prefix="/v1")
app.include_router(profile.router, prefix="/v1")
app.include_router(blocks.router, prefix="/v1")
app.include_router(sessions.router, prefix="/v1")
app.include_router(coach.router, prefix="/v1")
