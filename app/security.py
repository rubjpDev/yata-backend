"""Password hashing (Argon2id) and JWT (HS256) helpers."""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, status

from app.config import settings

_password_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]

_CREDENTIALS_ERROR_DETAIL = "Could not validate credentials"


def hash_password(plain: str) -> str:
    """Hash a plaintext password with Argon2id."""
    return _password_hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against an Argon2id hash.

    Returns False on mismatch instead of raising.
    """
    try:
        return _password_hasher.verify(hashed, plain)
    except VerifyMismatchError:
        return False


def _build_token(user_id: int, token_type: TokenType, ttl: timedelta) -> str:
    """Encode a JWT with sub/iat/exp/type claims."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + ttl,
        "type": token_type,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int) -> str:
    """Create a short-lived access token."""
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    return _build_token(user_id, "access", ttl)


def create_refresh_token(user_id: int) -> str:
    """Create a long-lived refresh token."""
    ttl = timedelta(days=settings.refresh_token_ttl_days)
    return _build_token(user_id, "refresh", ttl)


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """Decode and validate a JWT, enforcing its `type` claim.

    Raises HTTPException(401) on any signature, expiry, format, or type
    mismatch, with a generic detail (never reveals which check failed).
    """
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_CREDENTIALS_ERROR_DETAIL,
        ) from exc

    if payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_CREDENTIALS_ERROR_DETAIL,
        )

    return payload
