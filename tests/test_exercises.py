"""Tests for /v1/exercises (create custom, list system + own, filters, auth)."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Exercise


async def _auth_headers(client: AsyncClient, payload: dict[str, str]) -> dict[str, str]:
    """Register + login with the given payload and return Bearer auth headers."""
    await client.post("/v1/auth/register", json=payload)
    login = await client.post(
        "/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _second_user_payload() -> dict[str, str]:
    """A distinct registration payload for the 'other user' visibility test."""
    return {
        "email": "other@example.com",
        "password": "Sup3rSecret!",
        "display_name": "Other Athlete",
        "discipline": "powerlifting",
    }


async def test_create_exercise_returns_201_with_custom_resource(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """A valid POST creates a custom exercise owned by the current user."""
    headers = await _auth_headers(client, register_payload)

    response = await client.post(
        "/v1/exercises",
        headers=headers,
        json={"name": "Paused Bench", "category": "bench", "muscle_groups": ["chest"]},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Paused Bench"
    assert body["category"] == "bench"
    assert body["muscle_groups"] == ["chest"]
    assert body["is_custom"] is True
    assert body["created_by"] is not None


async def test_create_exercise_never_creates_system_exercise(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """The created resource always belongs to the user (created_by not NULL)."""
    headers = await _auth_headers(client, register_payload)

    response = await client.post(
        "/v1/exercises",
        headers=headers,
        json={"name": "Spoto Press", "category": "bench"},
    )

    assert response.status_code == 201
    assert response.json()["created_by"] is not None


async def test_create_exercise_empty_name_returns_422(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """An empty name is rejected with 422."""
    headers = await _auth_headers(client, register_payload)

    response = await client.post(
        "/v1/exercises",
        headers=headers,
        json={"name": "", "category": "squat"},
    )

    assert response.status_code == 422


async def test_create_exercise_invalid_category_returns_422(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """An unsupported category is rejected with 422."""
    headers = await _auth_headers(client, register_payload)

    response = await client.post(
        "/v1/exercises",
        headers=headers,
        json={"name": "Weird", "category": "cardio"},
    )

    assert response.status_code == 422


async def test_create_duplicate_custom_name_returns_409(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """Creating two customs with the same name for one user returns 409."""
    headers = await _auth_headers(client, register_payload)
    body = {"name": "Box Squat", "category": "squat"}

    first = await client.post("/v1/exercises", headers=headers, json=body)
    assert first.status_code == 201

    second = await client.post("/v1/exercises", headers=headers, json=body)
    assert second.status_code == 409


async def test_list_returns_system_and_own_not_others(
    client: AsyncClient,
    db_session: AsyncSession,
    register_payload: dict[str, str],
) -> None:
    """GET returns system rows + the user's customs, but not another user's."""
    # Seed a system exercise directly (migration seed does not run on SQLite).
    db_session.add(
        Exercise(
            name="Squat", category="squat", muscle_groups=["quads"], created_by=None
        )
    )
    await db_session.commit()

    # The "other" user creates a custom that must NOT be visible to us.
    other_headers = await _auth_headers(client, _second_user_payload())
    await client.post(
        "/v1/exercises",
        headers=other_headers,
        json={"name": "Other Only", "category": "accessory"},
    )

    # Our user creates their own custom.
    headers = await _auth_headers(client, register_payload)
    await client.post(
        "/v1/exercises",
        headers=headers,
        json={"name": "My Custom", "category": "accessory"},
    )

    response = await client.get("/v1/exercises", headers=headers)
    assert response.status_code == 200
    names = {row["name"] for row in response.json()}
    assert "Squat" in names  # system, visible to all
    assert "My Custom" in names  # our own custom
    assert "Other Only" not in names  # another user's custom, hidden


async def test_list_filters_by_category(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """The ?category= query parameter filters the result set."""
    headers = await _auth_headers(client, register_payload)
    await client.post(
        "/v1/exercises", headers=headers, json={"name": "A", "category": "squat"}
    )
    await client.post(
        "/v1/exercises", headers=headers, json={"name": "B", "category": "bench"}
    )

    response = await client.get("/v1/exercises?category=bench", headers=headers)
    assert response.status_code == 200
    categories = {row["category"] for row in response.json()}
    assert categories == {"bench"}


async def test_create_without_token_returns_401(client: AsyncClient) -> None:
    """POST without a token returns 401."""
    response = await client.post(
        "/v1/exercises", json={"name": "X", "category": "squat"}
    )
    assert response.status_code == 401


async def test_list_without_token_returns_401(client: AsyncClient) -> None:
    """GET without a token returns 401."""
    response = await client.get("/v1/exercises")
    assert response.status_code == 401
