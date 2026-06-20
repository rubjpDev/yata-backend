"""Tests for GET /v1/me."""

from httpx import AsyncClient

from app.core.security import create_access_token, create_refresh_token


async def test_me_with_valid_access_token_returns_identity_and_athlete_fields(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """A valid access token resolves the current user's identity + athlete fields."""
    register_response = await client.post("/v1/auth/register", json=register_payload)
    login_response = await client.post(
        "/v1/auth/login",
        json={
            "email": register_payload["email"],
            "password": register_payload["password"],
        },
    )
    access_token = login_response.json()["access_token"]

    response = await client.get(
        "/v1/me", headers={"Authorization": f"Bearer {access_token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == register_payload["email"]
    assert body["display_name"] == register_payload["display_name"]
    assert body["id"] == register_response.json()["id"]
    assert "hashed_password" not in body
    assert "role" not in body

    assert body["discipline"] == "powerlifting"
    assert body["unit"] == "kg"
    assert body["comp_style"] == "classic"
    assert body["equipment_owned"] == {
        "belt": False,
        "knee_sleeves": False,
        "knee_wraps": False,
        "wrist_wraps": False,
    }
    assert body["training_days_target"] is None


async def test_me_with_no_token_returns_401(client: AsyncClient) -> None:
    """No Authorization header returns 401."""
    response = await client.get("/v1/me")

    assert response.status_code == 401


async def test_me_with_invalid_token_returns_401(client: AsyncClient) -> None:
    """A malformed bearer token returns 401."""
    response = await client.get("/v1/me", headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401


async def test_me_with_refresh_token_returns_401(client: AsyncClient) -> None:
    """A refresh-type token used as a bearer access token returns 401."""
    refresh_token = create_refresh_token(user_id=1)

    response = await client.get(
        "/v1/me", headers={"Authorization": f"Bearer {refresh_token}"}
    )

    assert response.status_code == 401


async def test_me_with_unknown_subject_returns_401(client: AsyncClient) -> None:
    """An access token for a non-existent user id returns 401."""
    token = create_access_token(user_id=999999)

    response = await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
