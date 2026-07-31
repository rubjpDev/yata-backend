"""Tests for GET/PATCH /v1/profile: athlete-fields-only read/update + auth."""

from httpx import AsyncClient


async def _auth_headers(client: AsyncClient, payload: dict[str, str]) -> dict[str, str]:
    """Register + login with the given payload and return Bearer auth headers."""
    await client.post("/v1/auth/register", json=payload)
    login = await client.post(
        "/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_get_profile_returns_athlete_fields(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """GET returns exactly the athlete fields, never identity or auth fields."""
    headers = await _auth_headers(client, register_payload)

    response = await client.get("/v1/profile", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "discipline": "powerlifting",
        "unit": "kg",
        "comp_style": "classic",
        "equipment_owned": {
            "belt": False,
            "knee_sleeves": False,
            "knee_wraps": False,
            "wrist_wraps": False,
        },
        "training_days_target": None,
    }


async def test_patch_profile_updates_only_sent_fields(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """PATCH applies only the fields present in the payload (partial update)."""
    headers = await _auth_headers(client, register_payload)

    response = await client.patch(
        "/v1/profile",
        headers=headers,
        json={"training_days_target": 4, "unit": "lb"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["training_days_target"] == 4
    assert body["unit"] == "lb"
    assert body["comp_style"] == "classic"  # untouched


async def test_patch_profile_rejects_identity_and_admin_fields(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """Sending id/email/hashed_password/is_admin is a 422 (extra='forbid')."""
    headers = await _auth_headers(client, register_payload)

    response = await client.patch(
        "/v1/profile",
        headers=headers,
        json={"id": 999, "email": "hacker@example.com"},
    )
    assert response.status_code == 422

    response = await client.patch(
        "/v1/profile",
        headers=headers,
        json={"hashed_password": "x", "is_admin": True},
    )
    assert response.status_code == 422


async def test_get_profile_without_token_returns_401(client: AsyncClient) -> None:
    """GET without a token returns 401."""
    response = await client.get("/v1/profile")
    assert response.status_code == 401


async def test_patch_profile_without_token_returns_401(client: AsyncClient) -> None:
    """PATCH without a token returns 401."""
    response = await client.patch("/v1/profile", json={"unit": "lb"})
    assert response.status_code == 401
