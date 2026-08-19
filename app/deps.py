"""Shared FastAPI dependencies."""

from datetime import date
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.embeddings import FastEmbedClient
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
