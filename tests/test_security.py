"""Unit tests for app.security: hashing and JWT helpers."""

import time

import pytest
from fastapi import HTTPException

from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_hash_and_verify_round_trip() -> None:
    """A hashed password verifies correctly against the original plaintext."""
    hashed = hash_password("Sup3rSecret!")
    assert hashed != "Sup3rSecret!"
    assert verify_password("Sup3rSecret!", hashed) is True


def test_verify_wrong_password_returns_false() -> None:
    """Verification against the wrong plaintext returns False, never raises."""
    hashed = hash_password("Sup3rSecret!")
    assert verify_password("WrongPass1!", hashed) is False


def test_access_token_has_expected_claims_and_type() -> None:
    """Access tokens carry sub/iat/exp and type=access."""
    token = create_access_token(user_id=42)
    payload = decode_token(token, expected_type="access")

    assert payload["sub"] == "42"
    assert payload["type"] == "access"
    assert "iat" in payload
    assert "exp" in payload


def test_refresh_token_has_type_refresh() -> None:
    """Refresh tokens carry type=refresh."""
    token = create_refresh_token(user_id=7)
    payload = decode_token(token, expected_type="refresh")

    assert payload["type"] == "refresh"
    assert payload["sub"] == "7"


def test_decode_token_rejects_wrong_type() -> None:
    """An access token presented where a refresh token is expected is invalid."""
    token = create_access_token(user_id=1)
    with pytest.raises(HTTPException):
        decode_token(token, expected_type="refresh")


def test_decode_token_rejects_malformed_token() -> None:
    """A malformed token string raises HTTPException(401)."""
    with pytest.raises(HTTPException):
        decode_token("not-a-jwt", expected_type="access")


def test_decode_token_rejects_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired token raises HTTPException(401)."""
    from app.config import settings

    monkeypatch.setattr(settings, "access_token_ttl_minutes", 0)
    token = create_access_token(user_id=1)
    time.sleep(1)

    with pytest.raises(HTTPException):
        decode_token(token, expected_type="access")
