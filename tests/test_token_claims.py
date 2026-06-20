"""Tests asserting JWT claim sets carry no trainer/trainee role claim."""

from app.security import create_access_token, create_refresh_token, decode_token


def test_access_token_claims_are_exactly_sub_exp_iat_type() -> None:
    """A decoded access token's claim set is exactly sub/exp/iat/type."""
    token = create_access_token(user_id=42)
    payload = decode_token(token, expected_type="access")

    assert set(payload.keys()) == {"sub", "exp", "iat", "type"}
    assert payload["sub"] == "42"
    assert payload["type"] == "access"


def test_refresh_token_claims_are_exactly_sub_exp_iat_type() -> None:
    """A decoded refresh token's claim set is exactly sub/exp/iat/type."""
    token = create_refresh_token(user_id=7)
    payload = decode_token(token, expected_type="refresh")

    assert set(payload.keys()) == {"sub", "exp", "iat", "type"}
    assert payload["sub"] == "7"
    assert payload["type"] == "refresh"
