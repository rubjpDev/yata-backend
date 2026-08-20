"""PostgreSQL-only: the real checkpointer, the durable interrupt, the resume.

Skipped unless `YATA_TEST_PG_URL` points at a migrated database (`alembic
upgrade head`) whose `checkpoint*` tables were created by
`python -m scripts.setup_checkpointer`. Still the fake LLM (D-10): this file
proves the checkpointer and the gate, not the model.
"""

import os
from collections.abc import AsyncGenerator
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
from tests.test_coach import FakeLLMClient, _initial_state, _stub_retriever
from tests.test_rag import FakeEmbedder

PG_URL = os.getenv("YATA_TEST_PG_URL", "")
_PG_DSN = PG_URL.replace("postgresql+asyncpg://", "postgresql://")

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not PG_URL, reason="set YATA_TEST_PG_URL to run the pg tests"),
]


@pytest_asyncio.fixture
async def pg_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    """A session factory against the migrated PostgreSQL test database."""
    engine = create_async_engine(PG_URL)
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_athlete(
    pg_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[tuple[int, int]]:
    """A user + block + exercises + executed history, cleaned up after the test."""
    async with pg_session_factory() as session:
        user = User(
            email="coach-pg@example.com",
            hashed_password="x",
            display_name="Coach PG",
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
                Exercise(name="PG Squat", category="squat", muscle_groups=["quads"]),
                Exercise(name="PG Bench", category="bench", muscle_groups=["chest"]),
                Exercise(
                    name="PG Deadlift",
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

        exercise_ids_result = await session.execute(
            select(Exercise.id, Exercise.category).where(Exercise.name.like("PG %"))
        )
        exercise_ids = {
            category: ex_id for ex_id, category in exercise_ids_result.all()
        }
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
            await session.execute(delete(Exercise).where(Exercise.name.like("PG %")))
            await session.execute(delete(User).where(User.id == athlete_id))
            await session.commit()


async def _seed_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    athlete_id: int,
    block_id: int,
    thread_id: str,
) -> int:
    async with session_factory() as session:
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
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run.id


async def test_run_reaches_the_gate_and_a_checkpoint_row_exists(
    pg_session_factory: async_sessionmaker[AsyncSession],
    seeded_athlete: tuple[int, int],
) -> None:
    athlete_id, block_id = seeded_athlete
    thread_id = f"pg-thread-checkpoint-{block_id}"
    run_id = await _seed_run(
        pg_session_factory,
        athlete_id=athlete_id,
        block_id=block_id,
        thread_id=thread_id,
    )

    async with AsyncPostgresSaver.from_conn_string(_PG_DSN) as checkpointer:
        graph = build_coach_graph(
            llm=FakeLLMClient(),
            embedder=FakeEmbedder(),
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

        state = await graph.aget_state(config)
        assert state.next  # a pending task (human_gate) proves the pause persisted

    async with pg_session_factory() as session:
        checkpoint_count = await session.execute(
            select(TrainingWeek).where(
                TrainingWeek.block_id == block_id, TrainingWeek.status == "proposed"
            )
        )
        assert checkpoint_count.scalar_one_or_none() is not None


async def test_a_new_saver_instance_resumes_the_same_thread_and_activates_the_week(
    pg_session_factory: async_sessionmaker[AsyncSession],
    seeded_athlete: tuple[int, int],
) -> None:
    """The restart proof, at the mechanism level (R37): a brand-new
    `AsyncPostgresSaver` (standing in for a freshly restarted process) reads
    the persisted checkpoint and completes the decision. `tests/test_coach.py`
    already proves that same mechanism works with `InMemorySaver`; this proves
    it survives crossing a real connection boundary. The hand-run of T14
    (kill -9 the actual process) is the literal proof and is recorded verbatim
    in the impl report — no test in this repo can kill its own interpreter.
    """
    athlete_id, block_id = seeded_athlete
    thread_id = f"pg-thread-resume-{block_id}"
    run_id = await _seed_run(
        pg_session_factory,
        athlete_id=athlete_id,
        block_id=block_id,
        thread_id=thread_id,
    )
    config = {"configurable": {"thread_id": thread_id}}

    async with AsyncPostgresSaver.from_conn_string(_PG_DSN) as first_checkpointer:
        first_graph = build_coach_graph(
            llm=FakeLLMClient(),
            embedder=FakeEmbedder(),
            checkpointer=first_checkpointer,
            session_factory=pg_session_factory,
            retriever=_stub_retriever,
        )
        result = await first_graph.ainvoke(
            _initial_state(run_id=run_id, athlete_id=athlete_id, block_id=block_id),
            config=config,
        )
        assert "__interrupt__" in result
    # `first_checkpointer`'s connection is now closed — nothing keeps this
    # thread's state alive except what PostgreSQL itself persisted.

    from langgraph.types import Command

    async with AsyncPostgresSaver.from_conn_string(_PG_DSN) as second_checkpointer:
        second_graph = build_coach_graph(
            llm=FakeLLMClient(),
            embedder=FakeEmbedder(),
            checkpointer=second_checkpointer,
            session_factory=pg_session_factory,
            retriever=_stub_retriever,
        )
        await second_graph.ainvoke(Command(resume={"action": "accept"}), config=config)

    async with pg_session_factory() as session:
        week_result = await session.execute(
            select(TrainingWeek).where(
                TrainingWeek.block_id == block_id, TrainingWeek.proposal_id == run_id
            )
        )
        week = week_result.scalar_one()
        assert week.status.value == "active"

        run = await session.get(AgentRun, run_id)
        assert run is not None
        assert run.status == AgentRunStatus.accepted
