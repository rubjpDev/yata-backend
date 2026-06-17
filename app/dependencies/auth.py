"""Authentication dependencies for FastAPI routes."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidToken
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User
from app.services import auth_service

_bearer_scheme = HTTPBearer()

_CREDENTIALS_ERROR_DETAIL = "Could not validate credentials"


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the authenticated user from a Bearer access token, or 401."""
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except InvalidToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_CREDENTIALS_ERROR_DETAIL,
        ) from exc

    user = await auth_service.get_user_by_id(session, int(payload["sub"]))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_CREDENTIALS_ERROR_DETAIL,
        )

    return user
