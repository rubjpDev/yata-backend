"""Tests for POST /v1/auth/refresh."""

from httpx import AsyncClient


async def test_refresh_with_valid_refresh_token_returns_new_access_token(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """A valid refresh token yields 200 with a new access_token."""
    await client.post("/v1/auth/register", json=register_payload)
    login_response = await client.post(
        "/v1/auth/login",
        json={
            "email": register_payload["email"],
            "password": register_payload["password"],
        },
    )
    refresh_token = login_response.json()["refresh_token"]

    response = await client.post(
        "/v1/auth/refresh", json={"refresh_token": refresh_token}
    )

    assert response.status_code == 200
    assert "access_token" in response.json()


async def test_refresh_with_access_token_returns_401(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """Presenting an access token to /refresh returns 401."""
    await client.post("/v1/auth/register", json=register_payload)
    login_response = await client.post(
        "/v1/auth/login",
        json={
            "email": register_payload["email"],
            "password": register_payload["password"],
        },
    )
    access_token = login_response.json()["access_token"]

    response = await client.post(
        "/v1/auth/refresh", json={"refresh_token": access_token}
    )

    assert response.status_code == 401


async def test_refresh_with_invalid_token_returns_401(client: AsyncClient) -> None:
    """A malformed refresh token returns 401."""
    response = await client.post(
        "/v1/auth/refresh", json={"refresh_token": "not-a-jwt"}
    )

    assert response.status_code == 401
