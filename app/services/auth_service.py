"""Authentication business logic: register, login, refresh, lookup.

Raises domain errors from `app.core.errors`; never raises `HTTPException`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import EmailAlreadyExists, InvalidCredentials
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import Role, User
from app.repositories import user_repository
from app.schemas.auth import TokenPair


async def register(
    session: AsyncSession,
    email: str,
    password: str,
    role: Role,
    display_name: str,
) -> User:
    """Create a new user and its profile row, or raise EmailAlreadyExists."""
    existing = await user_repository.get_by_email(session, email)
    if existing is not None:
        raise EmailAlreadyExists("A user with this email already exists")

    hashed = hash_password(password)
    return await user_repository.create_user_with_profile(
        session,
        email=email,
        hashed_password=hashed,
        role=role,
        display_name=display_name,
    )


async def authenticate(session: AsyncSession, email: str, password: str) -> TokenPair:
    """Verify credentials and return a fresh access/refresh token pair."""
    user = await user_repository.get_by_email(session, email)
    if user is None or not verify_password(password, user.hashed_password):
        raise InvalidCredentials("Invalid credentials")

    return TokenPair(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id, user.role),
    )


async def refresh_access(session: AsyncSession, refresh_token: str) -> str:
    """Validate a refresh token and mint a new access token.

    Raises InvalidToken (via decode_token) on any malformed/expired/wrong-type
    token, and InvalidCredentials if the subject no longer maps to a user.
    """
    payload = decode_token(refresh_token, expected_type="refresh")
    user_id = int(payload["sub"])

    user = await user_repository.get_by_id(session, user_id)
    if user is None:
        raise InvalidCredentials("Invalid credentials")

    return create_access_token(user.id, user.role)


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    """Return the user with the given id, or None if not found."""
    return await user_repository.get_by_id(session, user_id)
