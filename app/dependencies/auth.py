"""Authentication and RBAC dependencies for FastAPI routes."""

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidToken
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import Role, User
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


def require_role(*roles: Role) -> Callable[..., Awaitable[User]]:
    """Build a dependency that authenticates and authorizes against `roles`."""

    async def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _dependency
