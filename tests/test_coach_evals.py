"""Golden structural evals against a REAL model (R58).

Skipped unless `YATA_TEST_LLM=1` **and** `YATA_TEST_PG_URL` are both set — a
real Bedrock call plus the real checkpointer. Every assertion is structural:
session count equals the proposed `days`, no set falls outside the engine's
landmark rules, every prescribed weight matches `engine.prescribe_week`
called in isolation. Nothing here asserts on the `rationale` or on any other
model-generated text (D11-12): text assertions on an LLM are a flaky test
with extra steps.

The double edge (D-10, R59): this marker skips silently without the
environment variables, exactly like `pg`. A green `-m llm` run that collected
zero tests proves nothing ran — check the collected count, not the colour.
"""

import os
from datetime import date

import pytest
import pytest_asyncio
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import engine
from app.deps import get_llm_client
from app.embeddings import FastEmbedClient
from app.graph import build_coach_graph
from app.models import (
    AgentRun,
    AgentRunStatus,
    Block,
    Exercise,
    TrainingSession,
    TrainingSet,
    TrainingWeek,
    User,
)
from tests.test_coach import _initial_state, _stub_retriever

PG_URL = os.getenv("YATA_TEST_PG_URL", "")
_PG_DSN = PG_URL.replace("postgresql+asyncpg://", "postgresql://")
_RUN_LLM = os.getenv("YATA_TEST_LLM", "") == "1"

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not (_RUN_LLM and PG_URL),
        reason="set YATA_TEST_LLM=1 and YATA_TEST_PG_URL to run the golden evals",
    ),
]


@pytest_asyncio.fixture
async def pg_session_factory():
    engine_ = create_async_engine(PG_URL)
    yield async_sessionmaker(bind=engine_, expire_on_commit=False)
    await engine_.dispose()


@pytest_asyncio.fixture
async def seeded_athlete(pg_session_factory: async_sessionmaker[AsyncSession]):
    """The same fixture shape as `tests/test_coach_pg.py`, kept local on purpose:
    an eval file should not depend on another test file's fixtures surviving
    a refactor."""
    async with pg_session_factory() as session:
        user = User(
            email="coach-eval@example.com",
            hashed_password="x",
            display_name="Coach Eval",
            discipline="powerlifting",
            equipment_owned={
                "belt": False,
                "knee_sleeves": False,
                "knee_wraps": False,
                "wrist_wraps": False,
            },
        )
        session.add(user)
        await session.flush()
        block = Block(
            athlete_id=user.id,
            intent="accumulation",
            planned_weeks=4,
            start_date=date(2026, 8, 3),
            status="active",
        )
        session.add(block)
        await session.flush()
        session.add_all(
            [
                Exercise(name="Eval Squat", category="squat", muscle_groups=["quads"]),
                Exercise(name="Eval Bench", category="bench", muscle_groups=["chest"]),
                Exercise(
                    name="Eval Deadlift",
                    category="deadlift",
                    muscle_groups=["hamstrings"],
                ),
            ]
        )
        await session.flush()
        week = TrainingWeek(
            block_id=block.id,
            athlete_id=user.id,
            week_index=1,
            days_planned=1,
            status="active",
        )
        session.add(week)
        await session.flush()
        training_session = TrainingSession(
            week_id=week.id,
            athlete_id=user.id,
            date=date(2026, 7, 1),
            session_type="full",
            status="completed",
        )
        session.add(training_session)
        await session.flush()
        ex_ids_result = await session.execute(
            select(Exercise.id, Exercise.category).where(Exercise.name.like("Eval %"))
        )
        exercise_ids = {category: ex_id for ex_id, category in ex_ids_result.all()}
        order = 1
        for lift, weight in (("squat", 100.0), ("bench", 80.0), ("deadlift", 120.0)):
            for _ in range(3):
                session.add(
                    TrainingSet(
                        session_id=training_session.id,
                        athlete_id=user.id,
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
        await session.commit()
        athlete_id, block_id = user.id, block.id

    try:
        yield athlete_id, block_id
    finally:
        async with pg_session_factory() as session:
            await session.execute(
                delete(TrainingSet).where(TrainingSet.athlete_id == athlete_id)
            )
            await session.execute(
                delete(TrainingSession).where(TrainingSession.athlete_id == athlete_id)
            )
            await session.execute(
                delete(TrainingWeek).where(TrainingWeek.athlete_id == athlete_id)
            )
            await session.execute(
                delete(AgentRun).where(AgentRun.athlete_id == athlete_id)
            )
            await session.execute(delete(Block).where(Block.athlete_id == athlete_id))
            await session.execute(delete(Exercise).where(Exercise.name.like("Eval %")))
            await session.execute(delete(User).where(User.id == athlete_id))
            await session.commit()


async def test_golden_proposal_is_structurally_sound(
    pg_session_factory: async_sessionmaker[AsyncSession],
    seeded_athlete: tuple[int, int],
) -> None:
    """A real model's proposal, checked for structure only — never text (R58)."""
    athlete_id, block_id = seeded_athlete
    thread_id = f"eval-thread-{block_id}"

    async with pg_session_factory() as session:
        run = AgentRun(
            athlete_id=athlete_id,
            block_id=block_id,
            thread_id=thread_id,
            status=AgentRunStatus.running,
            model="",
            prompt_version="v1",
            validation_verdict="",
            validation_errors=[],
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id

    async with AsyncPostgresSaver.from_conn_string(_PG_DSN) as checkpointer:
        graph = build_coach_graph(
            llm=get_llm_client(),
            embedder=FastEmbedClient(),
            checkpointer=checkpointer,
            session_factory=pg_session_factory,
            retriever=_stub_retriever,
        )
        config = {"configurable": {"thread_id": thread_id}}
        result = await graph.ainvoke(
            _initial_state(run_id=run_id, athlete_id=athlete_id, block_id=block_id),
            config=config,
        )

    assert "__interrupt__" in result
    proposal = result["proposal"]
    assert proposal is not None

    async with pg_session_factory() as session:
        week_result = await session.execute(
            select(TrainingWeek).where(
                TrainingWeek.block_id == block_id, TrainingWeek.status == "proposed"
            )
        )
        week = week_result.scalar_one()
        sessions_result = await session.execute(
            select(TrainingSession)
            .where(TrainingSession.week_id == week.id)
            .order_by(TrainingSession.date)
        )
        persisted_sessions = list(sessions_result.scalars().all())

        # Structural check 1: session count equals the proposed `days`.
        assert len(persisted_sessions) == proposal["days"]

        expected = engine.prescribe_week(
            proposal["intent"],
            proposal["days"],
            result["e1rm_by_lift"],
            week.week_index,
        )
        assert len(expected) == len(persisted_sessions)

        for persisted_session, expected_session in zip(
            persisted_sessions, expected, strict=True
        ):
            sets_result = await session.execute(
                select(TrainingSet)
                .where(TrainingSet.session_id == persisted_session.id)
                .order_by(TrainingSet.set_order)
            )
            persisted_sets = list(sets_result.scalars().all())
            assert len(persisted_sets) == len(expected_session.sets)
            for persisted_set, expected_set in zip(
                persisted_sets, expected_session.sets, strict=True
            ):
                # Structural check 2: every prescribed weight matches the
                # engine, called in isolation, exactly (never a float from
                # the model).
                assert persisted_set.prescribed_weight_kg == expected_set.weight_kg
                assert persisted_set.prescribed_reps == expected_set.reps
                assert persisted_set.prescribed_intensity == expected_set.intensity

        # Structural check 3: no set falls outside the engine's own landmark
        # rules for the intent it proposed.
        assert proposal["intent"] in engine.WEEK_TEMPLATES
