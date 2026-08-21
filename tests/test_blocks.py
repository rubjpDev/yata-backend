"""Tests for /v1/blocks: creation, engine==DB equality, athlete isolation, auth."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import engine
from app.models import Exercise, TrainingSet


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


_SEED_1RM = {"squat": 150.0, "bench": 100.0, "deadlift": 180.0}
_BLOCK_PAYLOAD = {
    "intent": "accumulation",
    "planned_weeks": 4,
    "start_date": "2026-08-03",
    "days": 4,
    "seed_1rm_kg": _SEED_1RM,
}


async def test_create_block_returns_201_with_week(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    """A valid block payload creates the block + week 1 and returns 201."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)

    response = await client.post("/v1/blocks", headers=headers, json=_BLOCK_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["intent"] == "accumulation"
    assert body["status"] == "active"
    assert len(body["weeks"]) == 1
    assert body["weeks"][0]["week_index"] == 1
    assert body["weeks"][0]["status"] == "active"


async def test_persisted_sets_match_engine_prescribe_week(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    """Persisted prescribed_* / set_order / set_type equal the isolated engine call."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)

    response = await client.post("/v1/blocks", headers=headers, json=_BLOCK_PAYLOAD)
    assert response.status_code == 201

    expected_sessions = engine.prescribe_week(
        "accumulation", 4, _SEED_1RM, week_index=1
    )
    expected_numbers = sorted(
        (s.set_order, s.set_type, s.reps, s.intensity, s.weight_kg)
        for session in expected_sessions
        for s in session.sets
    )

    result = await db_session.execute(select(TrainingSet))
    persisted_sets = result.scalars().all()
    persisted_numbers = sorted(
        (
            row.set_order,
            row.set_type.value,
            row.prescribed_reps,
            row.prescribed_intensity,
            row.prescribed_weight_kg,
        )
        for row in persisted_sets
    )

    assert persisted_numbers == expected_numbers


async def test_create_block_missing_1rm_returns_422_and_creates_nothing(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    """A lift with neither history nor seed is rejected before anything commits."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)

    payload = dict(_BLOCK_PAYLOAD, seed_1rm_kg={"squat": 150.0, "deadlift": 180.0})

    response = await client.post("/v1/blocks", headers=headers, json=payload)

    assert response.status_code == 422
    assert "bench" in response.json()["detail"]

    result = await db_session.execute(select(TrainingSet))
    assert result.scalars().first() is None


async def test_create_block_missing_system_exercise_returns_422_and_creates_nothing(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    """A needed lift with no system exercise (created_by NULL) is rejected first.

    Only squat/bench are seeded, so deadlift is missing; this must fail before
    any e1RM resolution or persistence (D9 debt: the missing_exercise branch
    in `create_block` had no dedicated test).
    """
    db_session.add_all(
        [
            Exercise(name="Squat", category="squat", muscle_groups=["quads"]),
            Exercise(name="Bench Press", category="bench", muscle_groups=["chest"]),
        ]
    )
    await db_session.commit()
    headers = await _auth_headers(client, register_payload)

    response = await client.post("/v1/blocks", headers=headers, json=_BLOCK_PAYLOAD)

    assert response.status_code == 422
    assert response.json()["detail"] == "no system exercise for lift 'deadlift'"

    result = await db_session.execute(select(TrainingSet))
    assert result.scalars().first() is None


async def test_get_own_block_returns_200(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    """GET on a block the athlete owns returns 200 with its week."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    create = await client.post("/v1/blocks", headers=headers, json=_BLOCK_PAYLOAD)
    block_id = create.json()["id"]

    response = await client.get(f"/v1/blocks/{block_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["id"] == block_id
    assert len(response.json()["weeks"]) == 1


async def test_other_athletes_block_returns_404(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    """A block belonging to another athlete responds 404, never 403."""
    await _seed_system_exercises(db_session)
    owner_headers = await _auth_headers(client, register_payload)
    create = await client.post("/v1/blocks", headers=owner_headers, json=_BLOCK_PAYLOAD)
    block_id = create.json()["id"]

    other_headers = await _auth_headers(client, _second_user_payload())
    response = await client.get(f"/v1/blocks/{block_id}", headers=other_headers)

    assert response.status_code == 404


async def test_get_block_without_token_returns_401(client: AsyncClient) -> None:
    """GET without a token returns 401."""
    response = await client.get("/v1/blocks/1")
    assert response.status_code == 401


async def test_post_block_without_token_returns_401(client: AsyncClient) -> None:
    """POST without a token returns 401."""
    response = await client.post("/v1/blocks", json=_BLOCK_PAYLOAD)
    assert response.status_code == 401
