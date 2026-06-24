"""Tests for POST /v1/auth/login."""

from httpx import AsyncClient


async def test_login_with_valid_credentials_returns_token_pair(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """A valid email/password pair returns 200 with access and refresh tokens."""
    await client.post("/v1/auth/register", json=register_payload)

    response = await client.post(
        "/v1/auth/login",
        json={
            "email": register_payload["email"],
            "password": register_payload["password"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body


async def test_login_with_unknown_email_returns_401(client: AsyncClient) -> None:
    """An unknown email returns 401 with a generic message."""
    response = await client.post(
        "/v1/auth/login",
        json={"email": "nobody@example.com", "password": "Sup3rSecret!"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


async def test_login_with_wrong_password_returns_401(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """A registered email with the wrong password returns 401, same message."""
    await client.post("/v1/auth/register", json=register_payload)

    response = await client.post(
        "/v1/auth/login",
        json={"email": register_payload["email"], "password": "WrongPass1!"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"
