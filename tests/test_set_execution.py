"""Tests for PATCH /v1/sets/{id}/execution: field-level authz (ADR-007) + status."""

from datetime import date

import pytest
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
        "email": "other-execution@example.com",
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


async def _create_block(client: AsyncClient, headers: dict[str, str]) -> None:
    response = await client.post("/v1/blocks", headers=headers, json=_BLOCK_PAYLOAD)
    assert response.status_code == 201


async def _set_ids_for_session(
    db_session: AsyncSession, session_date: date
) -> list[int]:
    result = await db_session.execute(
        select(TrainingSet.id)
        .join(TrainingSession, TrainingSet.session_id == TrainingSession.id)
        .where(TrainingSession.date == session_date)
        .order_by(TrainingSet.set_order)
    )
    return [row[0] for row in result.all()]


async def _session_status_for(
    client: AsyncClient, headers: dict[str, str], session_id: int
) -> str:
    response = await client.get(f"/v1/sessions/{session_id}", headers=headers)
    status: str = response.json()["status"]
    return status


async def _session_id_for_date(db_session: AsyncSession, session_date: date) -> int:
    result = await db_session.execute(
        select(TrainingSession).where(TrainingSession.date == session_date)
    )
    session_id: int = result.scalar_one().id
    return session_id


@pytest.mark.parametrize(
    "field,value",
    [
        ("prescribed_weight_kg", 999.0),
        ("set_order", 99),
        ("session_id", 1),
        ("status", "completed"),
    ],
)
async def test_forbidden_field_returns_422(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    field: str,
    value: object,
) -> None:
    """Each engine/prescription-owned field is rejected with 422 (extra='forbid')."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    await _create_block(client, headers)
    set_id = (await _set_ids_for_session(db_session, date(2026, 8, 3)))[0]

    response = await client.patch(
        f"/v1/sets/{set_id}/execution", headers=headers, json={field: value}
    )

    assert response.status_code == 422


async def test_valid_patch_updates_execution_and_keeps_prescribed_unchanged(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A valid PATCH sets executed_* without touching prescribed_*."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    await _create_block(client, headers)
    session_id = await _session_id_for_date(db_session, date(2026, 8, 3))
    set_id = (await _set_ids_for_session(db_session, date(2026, 8, 3)))[0]

    before = await client.get(f"/v1/sessions/{session_id}", headers=headers)
    prescribed_before = next(s for s in before.json()["sets"] if s["id"] == set_id)

    response = await client.patch(
        f"/v1/sets/{set_id}/execution",
        headers=headers,
        json={
            "executed_weight_kg": 102.5,
            "executed_reps": 5,
            "executed_intensity": 7.5,
            "completed_at": "2026-08-03T10:00:00Z",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["executed_weight_kg"] == 102.5
    assert body["executed_reps"] == 5
    assert body["executed_intensity"] == 7.5
    assert body["prescribed_weight_kg"] == prescribed_before["prescribed_weight_kg"]
    assert body["prescribed_reps"] == prescribed_before["prescribed_reps"]
    assert body["prescribed_intensity"] == prescribed_before["prescribed_intensity"]


async def test_other_athletes_set_returns_404(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """PATCHing a set belonging to another athlete responds 404, never 403."""
    await _seed_system_exercises(db_session)
    owner_headers = await _auth_headers(client, register_payload)
    await _create_block(client, owner_headers)
    set_id = (await _set_ids_for_session(db_session, date(2026, 8, 3)))[0]

    other_headers = await _auth_headers(client, _second_user_payload())
    response = await client.patch(
        f"/v1/sets/{set_id}/execution",
        headers=other_headers,
        json={"executed_weight_kg": 100.0, "executed_reps": 5},
    )

    assert response.status_code == 404


async def test_session_status_partial_after_some_sets_executed(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Executing one of a session's two sets flips status to partial."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    await _create_block(client, headers)
    session_id = await _session_id_for_date(db_session, date(2026, 8, 3))
    set_ids = await _set_ids_for_session(db_session, date(2026, 8, 3))
    assert len(set_ids) == 2

    response = await client.patch(
        f"/v1/sets/{set_ids[0]}/execution",
        headers=headers,
        json={
            "executed_weight_kg": 100.0,
            "executed_reps": 5,
            "executed_intensity": 7.0,
            "completed_at": "2026-08-03T10:00:00Z",
        },
    )
    assert response.status_code == 200

    assert await _session_status_for(client, headers, session_id) == "partial"


async def test_session_status_completed_after_all_sets_executed(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Executing every set in a session flips status to completed."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    await _create_block(client, headers)
    session_id = await _session_id_for_date(db_session, date(2026, 8, 5))
    set_ids = await _set_ids_for_session(db_session, date(2026, 8, 5))
    assert len(set_ids) == 2

    for set_id in set_ids:
        response = await client.patch(
            f"/v1/sets/{set_id}/execution",
            headers=headers,
            json={
                "executed_weight_kg": 80.0,
                "executed_reps": 5,
                "executed_intensity": 7.0,
                "completed_at": "2026-08-05T10:00:00Z",
            },
        )
        assert response.status_code == 200

    assert await _session_status_for(client, headers, session_id) == "completed"


async def test_session_status_prescribed_when_nothing_executed(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A freshly created session starts out as prescribed."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    await _create_block(client, headers)
    session_id = await _session_id_for_date(db_session, date(2026, 8, 7))

    assert await _session_status_for(client, headers, session_id) == "prescribed"
