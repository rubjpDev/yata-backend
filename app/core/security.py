"""Password hashing (Argon2id) and JWT (HS256) helpers."""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import settings
from app.core.errors import InvalidToken
from app.models.user import Role

_password_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]


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


def _build_token(
    user_id: int, role: Role, token_type: TokenType, ttl: timedelta
) -> str:
    """Encode a JWT with sub/role/iat/exp/type claims."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role.value,
        "iat": now,
        "exp": now + ttl,
        "type": token_type,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int, role: Role) -> str:
    """Create a short-lived access token."""
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    return _build_token(user_id, role, "access", ttl)


def create_refresh_token(user_id: int, role: Role) -> str:
    """Create a long-lived refresh token."""
    ttl = timedelta(days=settings.refresh_token_ttl_days)
    return _build_token(user_id, role, "refresh", ttl)


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """Decode and validate a JWT, enforcing its `type` claim.

    Raises InvalidToken on any signature, expiry, format, or type mismatch.
    """
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidToken("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidToken("Token is invalid") from exc

    if payload.get("type") != expected_type:
        raise InvalidToken("Unexpected token type")

    return payload
