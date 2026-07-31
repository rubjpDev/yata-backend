"""Tests for GET /v1/blocks/{id}/status: engine-backed volume zones + deload."""

from collections.abc import Callable
from datetime import date

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Exercise, TrainingSession, TrainingSet

_SEED_1RM = {"squat": 150.0, "bench": 100.0, "deadlift": 180.0}
_BLOCK_PAYLOAD = {
    "intent": "accumulation",
    "planned_weeks": 4,
    "start_date": "2026-08-03",
    "days": 4,
    "seed_1rm_kg": _SEED_1RM,
}


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
        "email": "other-status@example.com",
        "password": "Sup3rSecret!",
        "display_name": "Other Athlete",
        "discipline": "powerlifting",
    }


async def _seed_system_exercises(db_session: AsyncSession) -> None:
    """Insert the three system exercises: the SQLite test DB has no migration seed."""
    db_session.add_all(
        [
            Exercise(name="Squat", category="squat", muscle_groups=["quads"]),
            Exercise(name="Bench Press", category="bench", muscle_groups=["chest"]),
            Exercise(
                name="Deadlift", category="deadlift", muscle_groups=["hamstrings"]
            ),
        ]
    )
    await db_session.commit()


async def _create_block(client: AsyncClient, headers: dict[str, str]) -> int:
    response = await client.post("/v1/blocks", headers=headers, json=_BLOCK_PAYLOAD)
    assert response.status_code == 201
    block_id: int = response.json()["id"]
    return block_id


async def _first_set_id(db_session: AsyncSession, session_date: date) -> int:
    result = await db_session.execute(
        select(TrainingSet.id)
        .join(TrainingSession, TrainingSet.session_id == TrainingSession.id)
        .where(TrainingSession.date == session_date)
        .order_by(TrainingSet.set_order)
    )
    set_id: int = result.scalars().first()
    return set_id


async def test_status_with_no_executed_sets_returns_empty_zones_and_no_deload(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    set_today: Callable[[date], None],
) -> None:
    """A freshly created block has no executed sets: no zones, no deload."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    block_id = await _create_block(client, headers)
    set_today(date(2026, 8, 10))

    response = await client.get(f"/v1/blocks/{block_id}/status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["zones"] == {}
    assert body["deload"] is False
    assert body["reasons"] == []


async def test_status_reflects_hard_sets_in_volume_zones(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    set_today: Callable[[date], None],
) -> None:
    """One executed hard (RPE>=7) squat set lands 'quads' in below_MEV (< 8 sets)."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    block_id = await _create_block(client, headers)
    set_today(date(2026, 8, 10))
    set_id = await _first_set_id(db_session, date(2026, 8, 3))

    patch = await client.patch(
        f"/v1/sets/{set_id}/execution",
        headers=headers,
        json={
            "executed_weight_kg": 100.0,
            "executed_reps": 5,
            "executed_intensity": 7.0,
            "completed_at": "2026-08-03T10:00:00Z",
        },
    )
    assert patch.status_code == 200

    response = await client.get(f"/v1/blocks/{block_id}/status", headers=headers)

    assert response.status_code == 200
    assert response.json()["zones"]["quads"] == "below_MEV"


async def test_status_deload_fires_on_rpe_creep_at_same_load(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    set_today: Callable[[date], None],
) -> None:
    """Same (weight, reps) across two sessions with RPE creep >= 1.0 triggers deload."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    block_id = await _create_block(client, headers)

    first_set_id = await _first_set_id(db_session, date(2026, 8, 3))
    last_set_id = await _first_set_id(db_session, date(2026, 8, 9))

    for set_id, day, rpe in ((first_set_id, "03", 7.0), (last_set_id, "09", 8.5)):
        response = await client.patch(
            f"/v1/sets/{set_id}/execution",
            headers=headers,
            json={
                "executed_weight_kg": 100.0,
                "executed_reps": 5,
                "executed_intensity": rpe,
                "completed_at": f"2026-08-{day}T10:00:00Z",
            },
        )
        assert response.status_code == 200

    set_today(date(2026, 8, 10))
    response = await client.get(f"/v1/blocks/{block_id}/status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["deload"] is True
    assert any("RPE creep" in reason for reason in body["reasons"])


async def test_status_deload_does_not_fire_with_thin_history(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    set_today: Callable[[date], None],
) -> None:
    """A single easy executed set carries no deload signal."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    block_id = await _create_block(client, headers)
    set_today(date(2026, 8, 10))
    set_id = await _first_set_id(db_session, date(2026, 8, 3))

    patch = await client.patch(
        f"/v1/sets/{set_id}/execution",
        headers=headers,
        json={
            "executed_weight_kg": 100.0,
            "executed_reps": 5,
            "executed_intensity": 6.0,
            "completed_at": "2026-08-03T10:00:00Z",
        },
    )
    assert patch.status_code == 200

    response = await client.get(f"/v1/blocks/{block_id}/status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["deload"] is False
    assert body["reasons"] == []


async def test_status_other_athletes_block_returns_404(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A block belonging to another athlete responds 404, never 403."""
    await _seed_system_exercises(db_session)
    owner_headers = await _auth_headers(client, register_payload)
    block_id = await _create_block(client, owner_headers)

    other_headers = await _auth_headers(client, _second_user_payload())
    response = await client.get(f"/v1/blocks/{block_id}/status", headers=other_headers)

    assert response.status_code == 404


async def test_status_without_token_returns_401(client: AsyncClient) -> None:
    """GET .../status without a token returns 401."""
    response = await client.get("/v1/blocks/1/status")
    assert response.status_code == 401
