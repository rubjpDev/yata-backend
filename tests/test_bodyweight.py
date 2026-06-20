"""Tests for /v1/bodyweight (upsert 201/200, validation, ordering, filters, auth)."""

from datetime import date, timedelta

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


def _second_user_payload() -> dict[str, str]:
    return {
        "email": "other@example.com",
        "password": "Sup3rSecret!",
        "display_name": "Other Athlete",
        "discipline": "powerlifting",
    }


async def test_first_post_creates_returns_201(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """The first log for a date is created with 201."""
    headers = await _auth_headers(client, register_payload)

    response = await client.post(
        "/v1/bodyweight",
        headers=headers,
        json={"date": "2026-06-01", "weight_kg": 90.5},
    )

    assert response.status_code == 201
    assert response.json()["weight_kg"] == 90.5


async def test_repost_same_date_updates_returns_200(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """A second post for the same date updates the weight and returns 200."""
    headers = await _auth_headers(client, register_payload)
    await client.post(
        "/v1/bodyweight",
        headers=headers,
        json={"date": "2026-06-01", "weight_kg": 90.5},
    )

    response = await client.post(
        "/v1/bodyweight",
        headers=headers,
        json={"date": "2026-06-01", "weight_kg": 91.0},
    )

    assert response.status_code == 200
    assert response.json()["weight_kg"] == 91.0

    listing = await client.get("/v1/bodyweight", headers=headers)
    rows = [r for r in listing.json() if r["date"] == "2026-06-01"]
    assert len(rows) == 1  # upsert: one row, not two


async def test_non_positive_weight_returns_422(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """A weight of zero or below is rejected with 422."""
    headers = await _auth_headers(client, register_payload)

    response = await client.post(
        "/v1/bodyweight",
        headers=headers,
        json={"date": "2026-06-01", "weight_kg": 0},
    )

    assert response.status_code == 422


async def test_future_date_returns_422(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """A future date is rejected with 422."""
    headers = await _auth_headers(client, register_payload)
    future = (date.today() + timedelta(days=1)).isoformat()

    response = await client.post(
        "/v1/bodyweight",
        headers=headers,
        json={"date": future, "weight_kg": 90.0},
    )

    assert response.status_code == 422


async def test_list_ordered_date_desc(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """GET returns the athlete's logs ordered by date descending."""
    headers = await _auth_headers(client, register_payload)
    for d, w in [("2026-06-01", 90.0), ("2026-06-03", 91.0), ("2026-06-02", 90.5)]:
        await client.post(
            "/v1/bodyweight", headers=headers, json={"date": d, "weight_kg": w}
        )

    response = await client.get("/v1/bodyweight", headers=headers)
    assert response.status_code == 200
    dates = [r["date"] for r in response.json()]
    assert dates == ["2026-06-03", "2026-06-02", "2026-06-01"]


async def test_list_filters_from_to(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """The ?from=&to= bounds filter the result set inclusively."""
    headers = await _auth_headers(client, register_payload)
    for d in ["2026-06-01", "2026-06-05", "2026-06-10"]:
        await client.post(
            "/v1/bodyweight", headers=headers, json={"date": d, "weight_kg": 90.0}
        )

    response = await client.get(
        "/v1/bodyweight?from=2026-06-02&to=2026-06-08", headers=headers
    )
    assert response.status_code == 200
    dates = {r["date"] for r in response.json()}
    assert dates == {"2026-06-05"}


async def test_one_athlete_does_not_see_anothers_logs(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """A user's listing never includes another athlete's logs."""
    other_headers = await _auth_headers(client, _second_user_payload())
    await client.post(
        "/v1/bodyweight",
        headers=other_headers,
        json={"date": "2026-06-01", "weight_kg": 80.0},
    )

    headers = await _auth_headers(client, register_payload)
    await client.post(
        "/v1/bodyweight",
        headers=headers,
        json={"date": "2026-06-01", "weight_kg": 90.0},
    )

    response = await client.get("/v1/bodyweight", headers=headers)
    weights = {r["weight_kg"] for r in response.json()}
    assert weights == {90.0}  # not 80.0


async def test_post_without_token_returns_401(client: AsyncClient) -> None:
    """POST without a token returns 401."""
    response = await client.post(
        "/v1/bodyweight", json={"date": "2026-06-01", "weight_kg": 90.0}
    )
    assert response.status_code == 401


async def test_list_without_token_returns_401(client: AsyncClient) -> None:
    """GET without a token returns 401."""
    response = await client.get("/v1/bodyweight")
    assert response.status_code == 401
