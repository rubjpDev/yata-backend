"""Tests for /v1/sessions: injected 'today', prescribed-vs-executed view, isolation."""

from collections.abc import Callable
from datetime import date

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Exercise, TrainingSession

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
        "email": "other-sessions@example.com",
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


async def _session_id_for_date(db_session: AsyncSession, session_date: date) -> int:
    result = await db_session.execute(
        select(TrainingSession).where(TrainingSession.date == session_date)
    )
    session_id: int = result.scalar_one().id
    return session_id


async def test_get_today_session_returns_session_with_sets(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    set_today: Callable[[date], None],
) -> None:
    """The squat-day session (day_offset 0) is returned for the block's start date."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    await _create_block(client, headers)
    set_today(date(2026, 8, 3))

    response = await client.get("/v1/sessions/today", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["session_type"] == "squat"
    assert body["status"] == "prescribed"
    assert len(body["sets"]) == 2
    assert all(s["executed_weight_kg"] is None for s in body["sets"])
    assert all(s["prescribed_weight_kg"] is not None for s in body["sets"])


async def test_get_today_session_404_when_none_scheduled(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    set_today: Callable[[date], None],
) -> None:
    """A date with no session scheduled that day is a 404, not an empty body."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    await _create_block(client, headers)
    set_today(date(2026, 8, 4))

    response = await client.get("/v1/sessions/today", headers=headers)

    assert response.status_code == 404


async def test_get_session_by_id_returns_prescribed_vs_executed(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GET by id returns the same set-by-set prescribed/executed shape."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    await _create_block(client, headers)
    session_id = await _session_id_for_date(db_session, date(2026, 8, 3))

    response = await client.get(f"/v1/sessions/{session_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["id"] == session_id
    assert response.json()["session_type"] == "squat"


async def test_other_athletes_session_returns_404(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A session belonging to another athlete responds 404, never 403."""
    await _seed_system_exercises(db_session)
    owner_headers = await _auth_headers(client, register_payload)
    await _create_block(client, owner_headers)
    session_id = await _session_id_for_date(db_session, date(2026, 8, 3))

    other_headers = await _auth_headers(client, _second_user_payload())
    response = await client.get(f"/v1/sessions/{session_id}", headers=other_headers)

    assert response.status_code == 404


async def test_get_today_session_without_token_returns_401(
    client: AsyncClient,
) -> None:
    """GET /v1/sessions/today without a token returns 401."""
    response = await client.get("/v1/sessions/today")
    assert response.status_code == 401
