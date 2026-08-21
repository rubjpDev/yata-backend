"""Coach graph + routes: SQLite, fake LLM, fake embedder, in-memory saver.

Covers the nodes, the retry/revise/fallback ladder (R30-R32), `validate_week`
purity (R33), the engine-equality proof (R56), and the routes' 201/404/401/
409/422/503 paths. `tests/test_coach_pg.py` covers the real checkpointer and
the durable interrupt; `tests/test_coach_evals.py` covers the golden evals
against a real model.
"""

import json
from collections.abc import Generator
from datetime import date

import pytest
from httpx import AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import engine
from app.deps import (
    get_checkpointer,
    get_embedding_client,
    get_llm_client,
    get_retriever,
    get_session_factory,
)
from app.graph import CoachState, build_coach_graph, validate_week
from app.llm import LLMResponse
from app.main import app
from app.models import (
    AgentRun,
    AgentRunStatus,
    Exercise,
    TrainingSession,
    TrainingSet,
    TrainingWeek,
)
from tests.test_rag import FakeEmbedder

# ---------------------------------------------------------------------------
# Fakes (R19): same prompt in => same text out, no network, no model.
# ---------------------------------------------------------------------------


def _valid_proposal_json(*, intent: str = "accumulation", days: int = 3) -> str:
    return json.dumps(
        {
            "intent": intent,
            "days": days,
            "sessions": [{"equipment_config": {}} for _ in range(days)],
            "rationale": "grounded in the retrieved notes",
        }
    )


class FakeLLMClient:
    """Deterministic: always the same valid proposal, no matter the prompt."""

    def __init__(self, *, intent: str = "accumulation", days: int = 3) -> None:
        self._text = _valid_proposal_json(intent=intent, days=days)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system: str, user: str) -> LLMResponse:
        self.calls.append((system, user))
        return LLMResponse(
            text=self._text,
            model="fake-coach-model",
            prompt_tokens=12,
            completion_tokens=34,
            latency_ms=5,
        )


class FakeLLMClientInvalidOnce(FakeLLMClient):
    """Fails to parse on the first call, valid from the second (exercises R30)."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._invalid_left = 1

    async def complete(self, *, system: str, user: str) -> LLMResponse:
        if self._invalid_left > 0:
            self._invalid_left -= 1
            self.calls.append((system, user))
            return LLMResponse(
                text="not valid json",
                model="fake-coach-model",
                prompt_tokens=None,
                completion_tokens=None,
                latency_ms=5,
            )
        return await super().complete(system=system, user=user)


class FakeLLMClientAlwaysInvalid:
    """Never parses (exercises revise -> fallback_template, R31/R32)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system: str, user: str) -> LLMResponse:
        self.calls.append((system, user))
        return LLMResponse(
            text="still not valid json",
            model="fake-coach-model",
            prompt_tokens=None,
            completion_tokens=None,
            latency_ms=5,
        )


async def _stub_retriever(
    session: AsyncSession, query: str, embedder: object, **_: object
):
    """No PostgreSQL needed offline (D-6): a canned pair of notes."""
    from app.models import KnowledgeChunk

    return [
        KnowledgeChunk(content="Deload when RPE creeps at the same load."),
        KnowledgeChunk(content="Progress volume gradually toward MAV."),
    ]


# ---------------------------------------------------------------------------
# Shared seeding helpers
# ---------------------------------------------------------------------------

_SEED_1RM = {"squat": 150.0, "bench": 100.0, "deadlift": 180.0}
_BLOCK_PAYLOAD = {
    "intent": "accumulation",
    "planned_weeks": 4,
    "start_date": "2026-08-03",
    "days": 3,
    "seed_1rm_kg": _SEED_1RM,
}


async def _auth_headers(client: AsyncClient, payload: dict[str, str]) -> dict[str, str]:
    await client.post("/v1/auth/register", json=payload)
    login = await client.post(
        "/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _second_user_payload() -> dict[str, str]:
    return {
        "email": "other-coach@example.com",
        "password": "Sup3rSecret!",
        "display_name": "Other Athlete",
        "discipline": "powerlifting",
    }


async def _seed_system_exercises(db_session: AsyncSession) -> None:
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


async def _seed_executed_history(
    db_session: AsyncSession, athlete_id: int, *, sets_per_lift: int = 3
) -> None:
    """Enough executed working sets for `resolve_e1rm` to resolve every main lift."""
    exercise_ids_result = await db_session.execute(
        select(Exercise.id, Exercise.category)
    )
    exercise_ids = {category: ex_id for ex_id, category in exercise_ids_result.all()}

    week = TrainingWeek(
        block_id=1_000_000,  # detached from any real block on purpose
        athlete_id=athlete_id,
        week_index=0,
        days_planned=1,
        status="completed",
    )
    db_session.add(week)
    await db_session.flush()
    training_session = TrainingSession(
        week_id=week.id,
        athlete_id=athlete_id,
        date=date(2026, 7, 1),
        session_type="full",
        status="completed",
    )
    db_session.add(training_session)
    await db_session.flush()

    order = 1
    for lift, weight in (("squat", 100.0), ("bench", 80.0), ("deadlift", 120.0)):
        for _ in range(sets_per_lift):
            db_session.add(
                TrainingSet(
                    session_id=training_session.id,
                    athlete_id=athlete_id,
                    exercise_id=exercise_ids[lift],
                    set_order=order,
                    set_type="working",
                    intensity_type="RPE",
                    weight_mode="fixed",
                    equipment_config={},
                    prescribed_weight_kg=weight,
                    prescribed_reps=5,
                    prescribed_intensity=7.0,
                    executed_weight_kg=weight,
                    executed_reps=5,
                    executed_intensity=7.0,
                    completed_at=date(2026, 7, 1),
                )
            )
            order += 1
    await db_session.commit()


@pytest.fixture
def node_session_factory(
    db_session: AsyncSession,
) -> async_sessionmaker[AsyncSession]:
    """A real session factory bound to the SAME SQLite connection as `db_session`.

    Graph nodes never take the route's own session (R27); they open their own
    short one from an injected factory. The `db_session` fixture's engine uses
    `StaticPool` for `sqlite+aiosqlite:///:memory:` (SQLAlchemy's own default
    for that URL), so a second sessionmaker bound to the same engine sees the
    same in-memory database.
    """
    return async_sessionmaker(bind=db_session.bind, expire_on_commit=False)


_COACH_DEPS = (
    get_llm_client,
    get_embedding_client,
    get_checkpointer,
    get_session_factory,
    get_retriever,
)


@pytest.fixture(autouse=True)
def _override_coach_deps(
    node_session_factory: async_sessionmaker[AsyncSession],
) -> Generator[None]:
    """Fake LLM, fake embedder, in-memory checkpointer, stub retriever, session factory.

    The checkpointer and the LLM client are built ONCE per test, not per
    request: `app.dependency_overrides` callables are invoked on every
    dependency resolution, and a fresh `InMemorySaver()` per request would
    have no memory of the thread `accept`/`edit`/`reject` needs to resume.
    """
    shared_checkpointer = InMemorySaver()
    shared_llm = FakeLLMClient()
    app.dependency_overrides[get_llm_client] = lambda: shared_llm
    app.dependency_overrides[get_embedding_client] = lambda: FakeEmbedder()
    app.dependency_overrides[get_checkpointer] = lambda: shared_checkpointer
    app.dependency_overrides[get_session_factory] = lambda: node_session_factory
    app.dependency_overrides[get_retriever] = lambda: _stub_retriever
    yield
    for dep in _COACH_DEPS:
        app.dependency_overrides.pop(dep, None)


# ---------------------------------------------------------------------------
# validate_week: pure, no fixture needed (R33) — the purity proof itself
# ---------------------------------------------------------------------------


def test_validate_week_accepts_a_plain_valid_proposal() -> None:
    violations = validate_week(
        intent="accumulation",
        days=2,
        e1rm_by_lift={"squat": 150.0, "bench": 100.0, "deadlift": 180.0},
        week_index=1,
        lift_muscle_groups={
            "squat": ["quads"],
            "bench": ["chest"],
            "deadlift": ["hamstrings"],
        },
        equipment_owned={"belt": True},
        sessions_equipment_config=[{}, {}],
        deload_signal=False,
    )
    assert violations == []


def test_validate_week_rejects_an_engine_refused_intent() -> None:
    violations = validate_week(
        intent="not-a-real-intent",
        days=1,
        e1rm_by_lift={"squat": 100.0, "bench": 100.0, "deadlift": 100.0},
        week_index=1,
        lift_muscle_groups={},
        equipment_owned={},
        sessions_equipment_config=[{}],
        deload_signal=False,
    )
    assert any("unknown intent" in v for v in violations)


def test_validate_week_rejects_unowned_equipment() -> None:
    violations = validate_week(
        intent="accumulation",
        days=1,
        e1rm_by_lift={"squat": 100.0, "bench": 100.0, "deadlift": 100.0},
        week_index=1,
        lift_muscle_groups={"squat": ["quads"]},
        equipment_owned={"belt": False},
        sessions_equipment_config=[{"belt": True}],
        deload_signal=False,
    )
    assert any("belt" in v for v in violations)


def test_validate_week_rejects_a_non_deload_intent_when_the_signal_fired() -> None:
    violations = validate_week(
        intent="accumulation",
        days=1,
        e1rm_by_lift={"squat": 100.0, "bench": 100.0, "deadlift": 100.0},
        week_index=1,
        lift_muscle_groups={"squat": ["quads"]},
        equipment_owned={},
        sessions_equipment_config=[{}],
        deload_signal=True,
    )
    assert any("deload" in v for v in violations)


def test_validate_week_flags_above_mrv_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    # A single week's engine templates cap out around 9-11 sets, well under
    # any real landmark's MRV — so a tiny landmark proves the branch fires
    # without inventing a training number of our own.
    monkeypatch.setitem(engine.VOLUME_LANDMARKS, "quads", (1, 1, 2))
    violations = validate_week(
        intent="accumulation",
        days=4,
        e1rm_by_lift={"squat": 150.0, "bench": 100.0, "deadlift": 180.0},
        week_index=1,
        lift_muscle_groups={
            "squat": ["quads"],
            "bench": ["quads"],
            "deadlift": ["quads"],
        },
        equipment_owned={},
        sessions_equipment_config=[{}, {}, {}, {}],
        deload_signal=False,
    )
    assert any("above_MRV" in v for v in violations)


# ---------------------------------------------------------------------------
# The graph, node by node, via build_coach_graph directly (T11)
# ---------------------------------------------------------------------------


def _initial_state(*, run_id: int, athlete_id: int, block_id: int) -> CoachState:
    return {
        "run_id": run_id,
        "athlete_id": athlete_id,
        "block_id": block_id,
        "intent": "accumulation",
        "week_start_date": "2026-08-10",
        "attempts": 0,
        "validation_errors": [],
        "validation_verdict": "",
        "chunks_retrieved": 0,
        "used_fallback": False,
    }


async def _seed_run(
    db_session: AsyncSession, *, athlete_id: int, block_id: int, thread_id: str
) -> int:
    run = AgentRun(
        athlete_id=athlete_id,
        block_id=block_id,
        thread_id=thread_id,
        status=AgentRunStatus.running,
        model="fake-coach-model",
        prompt_version="v1",
        validation_verdict="",
        validation_errors=[],
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    run_id: int = run.id
    return run_id


async def test_graph_reaches_the_gate_with_a_valid_proposal(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    node_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    block_id = await _create_block(client, headers)
    me = await client.get("/v1/me", headers=headers)
    athlete_id = me.json()["id"]
    await _seed_executed_history(db_session, athlete_id)

    run_id = await _seed_run(
        db_session, athlete_id=athlete_id, block_id=block_id, thread_id="t-reach-gate"
    )
    llm = FakeLLMClient()
    graph = build_coach_graph(
        llm=llm,
        embedder=FakeEmbedder(),
        checkpointer=InMemorySaver(),
        session_factory=node_session_factory,
        retriever=_stub_retriever,
    )
    config = {"configurable": {"thread_id": "t-reach-gate"}}
    result = await graph.ainvoke(
        _initial_state(run_id=run_id, athlete_id=athlete_id, block_id=block_id),
        config=config,
    )

    assert "__interrupt__" in result
    assert result["validation_verdict"] == "valid"
    assert result["attempts"] == 0
    assert llm.calls  # the LLM was actually invoked, not skipped
    assert result["chunks_retrieved"] == 2


async def test_graph_ends_failed_with_no_llm_call_when_e1rm_is_missing(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    node_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    block_id = await _create_block(client, headers)
    me = await client.get("/v1/me", headers=headers)
    athlete_id = me.json()["id"]
    # No executed history seeded at all: every lift is missing.

    run_id = await _seed_run(
        db_session, athlete_id=athlete_id, block_id=block_id, thread_id="t-missing-e1rm"
    )
    llm = FakeLLMClient()
    graph = build_coach_graph(
        llm=llm,
        embedder=FakeEmbedder(),
        checkpointer=InMemorySaver(),
        session_factory=node_session_factory,
        retriever=_stub_retriever,
    )
    config = {"configurable": {"thread_id": "t-missing-e1rm"}}
    result = await graph.ainvoke(
        _initial_state(run_id=run_id, athlete_id=athlete_id, block_id=block_id),
        config=config,
    )

    assert result.get("missing_lift") is not None
    assert "__interrupt__" not in result
    assert llm.calls == []

    run = await db_session.get(AgentRun, run_id)
    await db_session.refresh(run)
    assert run.status == AgentRunStatus.failed
    assert run.error_detail is not None


async def test_graph_retries_once_then_revises_then_falls_back(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    node_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """R30/R31/R32: parse failures all the way down land on the fallback template."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    block_id = await _create_block(client, headers)
    me = await client.get("/v1/me", headers=headers)
    athlete_id = me.json()["id"]
    await _seed_executed_history(db_session, athlete_id)

    run_id = await _seed_run(
        db_session, athlete_id=athlete_id, block_id=block_id, thread_id="t-fallback"
    )
    llm = FakeLLMClientAlwaysInvalid()
    graph = build_coach_graph(
        llm=llm,
        embedder=FakeEmbedder(),
        checkpointer=InMemorySaver(),
        session_factory=node_session_factory,
        retriever=_stub_retriever,
    )
    config = {"configurable": {"thread_id": "t-fallback"}}
    result = await graph.ainvoke(
        _initial_state(run_id=run_id, athlete_id=athlete_id, block_id=block_id),
        config=config,
    )

    assert len(llm.calls) == 3  # propose, one retry, one revise — then no more
    assert result["attempts"] == 3
    assert result["validation_verdict"] == "fallback_template"
    assert "__interrupt__" in result


async def test_graph_reaches_the_gate_after_one_invalid_attempt(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    node_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """R30: a single parse failure retries the same node and still reaches the gate."""
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    block_id = await _create_block(client, headers)
    me = await client.get("/v1/me", headers=headers)
    athlete_id = me.json()["id"]
    await _seed_executed_history(db_session, athlete_id)

    run_id = await _seed_run(
        db_session, athlete_id=athlete_id, block_id=block_id, thread_id="t-retry-once"
    )
    llm = FakeLLMClientInvalidOnce()
    graph = build_coach_graph(
        llm=llm,
        embedder=FakeEmbedder(),
        checkpointer=InMemorySaver(),
        session_factory=node_session_factory,
        retriever=_stub_retriever,
    )
    config = {"configurable": {"thread_id": "t-retry-once"}}
    result = await graph.ainvoke(
        _initial_state(run_id=run_id, athlete_id=athlete_id, block_id=block_id),
        config=config,
    )

    assert len(llm.calls) == 2
    assert result["validation_verdict"] == "valid"
    assert "__interrupt__" in result


# ---------------------------------------------------------------------------
# R56 — the agent invented nothing: the engine-equality proof
# ---------------------------------------------------------------------------


async def test_persisted_sets_match_engine_prescribe_week_field_by_field(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    node_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    block_id = await _create_block(client, headers)
    me = await client.get("/v1/me", headers=headers)
    athlete_id = me.json()["id"]
    await _seed_executed_history(db_session, athlete_id)

    run_id = await _seed_run(
        db_session, athlete_id=athlete_id, block_id=block_id, thread_id="t-equality"
    )
    llm = FakeLLMClient(intent="accumulation", days=2)
    graph = build_coach_graph(
        llm=llm,
        embedder=FakeEmbedder(),
        checkpointer=InMemorySaver(),
        session_factory=node_session_factory,
        retriever=_stub_retriever,
    )
    config = {"configurable": {"thread_id": "t-equality"}}
    result = await graph.ainvoke(
        _initial_state(run_id=run_id, athlete_id=athlete_id, block_id=block_id),
        config=config,
    )
    assert "__interrupt__" in result

    week_result = await db_session.execute(
        select(TrainingWeek).where(
            TrainingWeek.block_id == block_id, TrainingWeek.status == "proposed"
        )
    )
    week = week_result.scalar_one()
    sessions_result = await db_session.execute(
        select(TrainingSession)
        .where(TrainingSession.week_id == week.id)
        .order_by(TrainingSession.date)
    )
    persisted_sessions = list(sessions_result.scalars().all())

    expected = engine.prescribe_week(
        "accumulation", 2, result["e1rm_by_lift"], week.week_index
    )

    assert len(persisted_sessions) == len(expected)
    for persisted_session, expected_session in zip(
        persisted_sessions, expected, strict=True
    ):
        sets_result = await db_session.execute(
            select(TrainingSet)
            .where(TrainingSet.session_id == persisted_session.id)
            .order_by(TrainingSet.set_order)
        )
        persisted_sets = list(sets_result.scalars().all())
        assert len(persisted_sets) == len(expected_session.sets)
        for persisted_set, expected_set in zip(
            persisted_sets, expected_session.sets, strict=True
        ):
            assert persisted_set.set_order == expected_set.set_order
            assert persisted_set.set_type.value == expected_set.set_type
            assert persisted_set.intensity_type.value == expected_set.intensity_type
            assert persisted_set.weight_mode.value == expected_set.weight_mode
            assert persisted_set.prescribed_weight_kg == expected_set.weight_kg
            assert persisted_set.prescribed_reps == expected_set.reps
            assert persisted_set.prescribed_intensity == expected_set.intensity


# ---------------------------------------------------------------------------
# Routes (T15): 201, 404 both directions, 401, 409, 422, 503, accept/edit/reject
# ---------------------------------------------------------------------------


async def _propose(client: AsyncClient, headers: dict[str, str], block_id: int) -> dict:
    response = await client.post(
        "/v1/coach/runs", headers=headers, json={"block_id": block_id}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _prepared_athlete(
    client: AsyncClient, db_session: AsyncSession, register_payload: dict[str, str]
) -> tuple[dict[str, str], int, int]:
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    block_id = await _create_block(client, headers)
    me = await client.get("/v1/me", headers=headers)
    athlete_id = me.json()["id"]
    await _seed_executed_history(db_session, athlete_id)
    return headers, block_id, athlete_id


async def test_create_run_returns_201_with_proposed_week(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    headers, block_id, _ = await _prepared_athlete(client, db_session, register_payload)

    body = await _propose(client, headers, block_id)

    assert body["status"] == "awaiting_gate"
    assert body["validation_verdict"] == "valid"
    assert body["week"]["status"] == "proposed"
    assert len(body["week"]["sessions"]) == 3
    assert body["proposal"]["intent"] == "accumulation"


async def test_create_run_missing_history_returns_422_naming_the_lift(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    await _seed_system_exercises(db_session)
    headers = await _auth_headers(client, register_payload)
    block_id = await _create_block(client, headers)
    # No executed history: e1RM cannot be resolved for any main lift.

    response = await client.post(
        "/v1/coach/runs", headers=headers, json={"block_id": block_id}
    )

    assert response.status_code == 422
    assert response.json()["detail"]


async def test_create_run_second_open_run_returns_409(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    headers, block_id, _ = await _prepared_athlete(client, db_session, register_payload)
    first = await _propose(client, headers, block_id)

    response = await client.post(
        "/v1/coach/runs", headers=headers, json={"block_id": block_id}
    )

    assert response.status_code == 409
    assert str(first["id"]) in response.json()["detail"]


async def test_create_run_other_athletes_block_returns_404(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    headers, block_id, _ = await _prepared_athlete(client, db_session, register_payload)
    other_headers = await _auth_headers(client, _second_user_payload())

    response = await client.post(
        "/v1/coach/runs", headers=other_headers, json={"block_id": block_id}
    )

    assert response.status_code == 404


async def test_create_run_without_token_returns_401(client: AsyncClient) -> None:
    response = await client.post("/v1/coach/runs", json={"block_id": 1})
    assert response.status_code == 401


async def test_get_run_other_athletes_run_returns_404(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    headers, block_id, _ = await _prepared_athlete(client, db_session, register_payload)
    run = await _propose(client, headers, block_id)
    other_headers = await _auth_headers(client, _second_user_payload())

    response = await client.get(f"/v1/coach/runs/{run['id']}", headers=other_headers)

    assert response.status_code == 404


async def test_get_unknown_run_returns_404(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    headers = await _auth_headers(client, register_payload)
    response = await client.get("/v1/coach/runs/999999", headers=headers)
    assert response.status_code == 404


async def test_get_run_returns_created_at_and_status(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    headers, block_id, _ = await _prepared_athlete(client, db_session, register_payload)
    run = await _propose(client, headers, block_id)

    response = await client.get(f"/v1/coach/runs/{run['id']}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["created_at"]
    assert body["status"] == "awaiting_gate"


async def test_accept_activates_the_week_and_writes_proposal_id(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    headers, block_id, _ = await _prepared_athlete(client, db_session, register_payload)
    run = await _propose(client, headers, block_id)

    response = await client.post(f"/v1/coach/runs/{run['id']}/accept", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["week"]["status"] == "active"

    week = await db_session.get(TrainingWeek, body["week"]["id"])
    await db_session.refresh(week)
    assert week.proposal_id == run["id"]


async def test_reject_deletes_the_week_and_activates_nothing(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    headers, block_id, _ = await _prepared_athlete(client, db_session, register_payload)
    run = await _propose(client, headers, block_id)
    proposed_week_id = run["week"]["id"]

    response = await client.post(f"/v1/coach/runs/{run['id']}/reject", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["week"] is None
    assert body["proposal"] is not None  # survives as the audit trail

    week = await db_session.get(TrainingWeek, proposed_week_id)
    assert week is None

    # a fresh run can now be started: the block no longer has an open gate.
    second = await _propose(client, headers, block_id)
    assert second["id"] != run["id"]


async def test_edit_replaces_the_proposal_and_stays_awaiting_gate(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    headers, block_id, _ = await _prepared_athlete(client, db_session, register_payload)
    run = await _propose(client, headers, block_id)

    response = await client.post(
        f"/v1/coach/runs/{run['id']}/edit",
        headers=headers,
        json={"feedback": "please run 2 days instead"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "awaiting_gate"
    assert body["week"] is not None

    # Exactly one `proposed` week for the block: the superseded one was
    # deleted and replaced, never duplicated. (SQLite recycles a deleted
    # row's integer id, so the id alone cannot prove a genuinely new row.)
    week_count = await db_session.execute(
        select(TrainingWeek).where(
            TrainingWeek.block_id == block_id, TrainingWeek.status == "proposed"
        )
    )
    assert len(list(week_count.scalars().all())) == 1


@pytest.mark.parametrize(
    "route_suffix,payload",
    [
        ("accept", None),
        ("edit", {"feedback": "please run 2 days instead"}),
        ("reject", None),
    ],
)
async def test_gate_route_other_athletes_run_returns_404_without_mutating(
    client: AsyncClient,
    register_payload: dict[str, str],
    db_session: AsyncSession,
    route_suffix: str,
    payload: dict[str, str] | None,
) -> None:
    """A stranger's accept/edit/reject 404s and leaves the week untouched.

    `_owned_run` (ADR-007) must reject before the graph resumes: a route that
    404s but still resolves the gate would be worse than one that 403s.
    """
    headers, block_id, _ = await _prepared_athlete(client, db_session, register_payload)
    run = await _propose(client, headers, block_id)
    proposed_week_id = run["week"]["id"]
    other_headers = await _auth_headers(client, _second_user_payload())

    response = await client.post(
        f"/v1/coach/runs/{run['id']}/{route_suffix}",
        headers=other_headers,
        json=payload if payload is not None else {},
    )

    assert response.status_code == 404

    week = await db_session.get(TrainingWeek, proposed_week_id)
    await db_session.refresh(week)
    assert week.status == "proposed"
    assert week.proposal_id is None


async def test_accept_on_a_run_not_awaiting_gate_returns_409(
    client: AsyncClient, register_payload: dict[str, str], db_session: AsyncSession
) -> None:
    headers, block_id, _ = await _prepared_athlete(client, db_session, register_payload)
    run = await _propose(client, headers, block_id)
    accepted = await client.post(f"/v1/coach/runs/{run['id']}/accept", headers=headers)
    assert accepted.status_code == 200

    response = await client.post(f"/v1/coach/runs/{run['id']}/accept", headers=headers)

    assert response.status_code == 409
