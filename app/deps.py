"""Shared FastAPI dependencies."""

from datetime import date
from functools import lru_cache
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import rag
from app.config import settings
from app.db import AsyncSessionLocal, get_db
from app.embeddings import FastEmbedClient
from app.graph import Retriever
from app.llm import BedrockConverseClient, LLMClient
from app.models import User
from app.security import decode_token

_bearer_scheme = HTTPBearer()

_CREDENTIALS_ERROR_DETAIL = "Could not validate credentials"


def get_today() -> date:
    """Today's date, injected so routes never call `date.today()` directly.

    Overridable in tests via `app.dependency_overrides[get_today]`; also
    supplies `now` to the block-status route.
    """
    return date.today()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the authenticated user from a Bearer access token, or 401."""
    payload = decode_token(credentials.credentials, expected_type="access")

    result = await session.execute(select(User).where(User.id == int(payload["sub"])))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_CREDENTIALS_ERROR_DETAIL,
        )

    return user


@lru_cache(maxsize=1)
def get_embedding_client() -> FastEmbedClient:
    """The process-wide embedding client, built on first call, never at import.

    Cached because building it downloads and loads a ~130 MB model; injected
    explicitly (never constructed inside `app.rag`) so tests pass a fake instead.
    """
    return FastEmbedClient()


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """The process-wide LLM client, built on first call, never at import.

    Cached in the same injectable-resource slot as `get_today` and
    `get_embedding_client`; every coach route takes it via `Depends`, and
    tests replace it with `app.dependency_overrides` (R18).
    """
    return BedrockConverseClient()


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """The session factory graph nodes open their own short sessions from.

    A live `AsyncSession` cannot cross an `interrupt()` that may last days
    (R27), so the coach graph never takes `Depends(get_db)`'s session — every
    node opens and closes its own. Overridable in tests so nodes reach the
    same in-memory SQLite engine as the route's `Depends(get_db)` session.
    """
    return AsyncSessionLocal


def get_retriever() -> Retriever:
    """`app.rag.retrieve`, unmodified (R26), injected so tests can swap a stub.

    Without this indirection every route-level coach test would need
    PostgreSQL, because `retrieve` computes cosine distance in the database
    (D-6). The function itself is never changed.
    """
    return rag.retrieve


_pg_checkpointer_cm: Any = None
_pg_checkpointer: BaseCheckpointSaver[Any] | None = None


async def get_checkpointer() -> BaseCheckpointSaver[Any]:
    """The process-wide LangGraph checkpointer, opened once, reused every run.

    `AsyncPostgresSaver` is built on psycopg 3, not asyncpg (R62.3):
    `settings.database_url` is `postgresql+asyncpg://…`, so the DSN handed to
    the saver is derived by stripping the driver suffix — no second setting.
    Never calls `.setup()` (R12): that runs exactly once, from
    `python -m scripts.setup_checkpointer`. Overridable in tests with an
    in-memory saver, so the default suite never opens a real connection.
    """
    global _pg_checkpointer_cm, _pg_checkpointer
    if _pg_checkpointer is None:
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pg_checkpointer_cm = AsyncPostgresSaver.from_conn_string(dsn)
        _pg_checkpointer = await _pg_checkpointer_cm.__aenter__()
    return _pg_checkpointer
