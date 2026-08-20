"""Coach routes: propose a week, gate it, resume it (flat, ADR-015).

`POST /v1/coach/runs` runs the whole graph inline, synchronously, in this one
request-response cycle (R66; deferred async execution is yata-0014). Every
route: route function -> `AsyncSession` -> Pydantic, `HTTPException` at the
point of detection, no repository, no service layer (R51). Someone else's
run or block is 404, never 403 (ADR-007, R45).
"""

from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import graph as coach_graph
from app.config import settings
from app.db import get_db
from app.deps import (
    get_checkpointer,
    get_current_user,
    get_embedding_client,
    get_llm_client,
    get_retriever,
    get_session_factory,
    get_today,
)
from app.embeddings import EmbeddingClient
from app.graph import CoachState, Retriever
from app.llm import LLMClient, LLMRequestError
from app.models import (
    AgentRun,
    AgentRunStatus,
    Block,
    Exercise,
    TrainingSession,
    TrainingSet,
    TrainingWeek,
    User,
    WeekStatus,
)
from app.schemas import (
    CoachEditRequest,
    CoachRunCreate,
    CoachRunRead,
    ProposedWeekRead,
    SessionRead,
    SetRead,
    WeekProposal,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Materializing the proposed week — called from the graph's `human_gate` node
# ---------------------------------------------------------------------------


async def _system_exercise_ids(session: AsyncSession) -> dict[str, int]:
    """Map each main-lift category to its system exercise id (created_by NULL)."""
    result = await session.execute(
        select(Exercise.id, Exercise.category).where(Exercise.created_by.is_(None))
    )
    return {category: exercise_id for exercise_id, category in result.all()}


async def materialize_proposed_week(session: AsyncSession, state: CoachState) -> int:
    """Insert the proposed week, or return the id of one already inserted.

    Called from `app.graph`'s `human_gate` node (imported there lazily, to
    avoid a cycle), on the SAME session that node opened. Idempotent by
    construction: LangGraph replays a node **from its start** on every resume
    of an `interrupt()` inside it (R37's mechanism), so a plain INSERT here
    would double the week on the very first resume — the existence check
    makes the insert run at most once per planning cycle.

    Every prescribed field is assigned straight from the engine's own output
    (R52): no arithmetic, no literal, nothing derived from the LLM.
    """
    existing = await session.execute(
        select(TrainingWeek).where(
            TrainingWeek.block_id == state["block_id"],
            TrainingWeek.week_index == state["week_index"],
            TrainingWeek.status == WeekStatus.proposed,
        )
    )
    week = existing.scalar_one_or_none()
    if week is not None:
        return week.id

    proposal = state["proposal"]
    assert proposal is not None
    prescribed_sessions = state.get("prescribed_sessions") or []
    week_start = date.fromisoformat(state["week_start_date"])

    week = TrainingWeek(
        block_id=state["block_id"],
        athlete_id=state["athlete_id"],
        week_index=state["week_index"],
        days_planned=proposal["days"],
        status=WeekStatus.proposed,
    )
    session.add(week)
    await session.flush()

    exercise_ids = await _system_exercise_ids(session)
    for index, prescribed_session in enumerate(prescribed_sessions):
        equipment_config = proposal["sessions"][index]["equipment_config"]
        training_session = TrainingSession(
            week_id=week.id,
            athlete_id=state["athlete_id"],
            date=week_start + timedelta(days=prescribed_session["day_offset"]),
            session_type=prescribed_session["session_type"],
        )
        session.add(training_session)
        await session.flush()

        for prescribed_set in prescribed_session["sets"]:
            session.add(
                TrainingSet(
                    session_id=training_session.id,
                    athlete_id=state["athlete_id"],
                    exercise_id=exercise_ids[prescribed_set["lift"]],
                    set_order=prescribed_set["set_order"],
                    set_type=prescribed_set["set_type"],
                    intensity_type=prescribed_set["intensity_type"],
                    weight_mode=prescribed_set["weight_mode"],
                    equipment_config=equipment_config,
                    prescribed_weight_kg=prescribed_set["weight_kg"],
                    prescribed_reps=prescribed_set["reps"],
                    prescribed_intensity=prescribed_set["intensity"],
                )
            )
    await session.commit()
    return week.id


# ---------------------------------------------------------------------------
# Ownership lookups — 404, never 403 (ADR-007)
# ---------------------------------------------------------------------------


async def _owned_block(session: AsyncSession, block_id: int, user_id: int) -> Block:
    result = await session.execute(
        select(Block).where(Block.id == block_id, Block.athlete_id == user_id)
    )
    block = result.scalar_one_or_none()
    if block is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Block not found"
        )
    return block


async def _owned_run(session: AsyncSession, run_id: int, user_id: int) -> AgentRun:
    result = await session.execute(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.athlete_id == user_id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found"
        )
    return run


# ---------------------------------------------------------------------------
# Reading a run + its (proposed or activated) week
# ---------------------------------------------------------------------------


async def _run_week(session: AsyncSession, run: AgentRun) -> TrainingWeek | None:
    """The week this run produced, however it is currently shaped.

    While `awaiting_gate` there is exactly one `proposed` week for the block
    (the 409 rule guarantees no second one). Once `accepted`, the FK is the
    unambiguous link. `rejected`/`failed` leave nothing behind.
    """
    if run.status == AgentRunStatus.accepted:
        result = await session.execute(
            select(TrainingWeek).where(TrainingWeek.proposal_id == run.id)
        )
        return result.scalar_one_or_none()
    if run.status == AgentRunStatus.awaiting_gate:
        result = await session.execute(
            select(TrainingWeek).where(
                TrainingWeek.block_id == run.block_id,
                TrainingWeek.status == WeekStatus.proposed,
            )
        )
        return result.scalar_one_or_none()
    return None


async def _week_read(session: AsyncSession, week: TrainingWeek) -> ProposedWeekRead:
    sessions_result = await session.execute(
        select(TrainingSession)
        .where(TrainingSession.week_id == week.id)
        .order_by(TrainingSession.date)
    )
    session_reads = []
    for training_session in sessions_result.scalars().all():
        sets_result = await session.execute(
            select(TrainingSet)
            .where(TrainingSet.session_id == training_session.id)
            .order_by(TrainingSet.set_order)
        )
        session_reads.append(
            SessionRead(
                id=training_session.id,
                week_id=training_session.week_id,
                date=training_session.date,
                session_type=training_session.session_type,
                status=training_session.status,
                created_at=training_session.created_at,
                sets=[SetRead.model_validate(s) for s in sets_result.scalars().all()],
            )
        )
    return ProposedWeekRead(
        id=week.id,
        week_index=week.week_index,
        days_planned=week.days_planned,
        status=week.status,
        created_at=week.created_at,
        sessions=session_reads,
    )


async def _run_read(session: AsyncSession, run: AgentRun) -> CoachRunRead:
    week = await _run_week(session, run)
    week_read = await _week_read(session, week) if week is not None else None
    proposal = WeekProposal.model_validate(run.proposal) if run.proposal else None
    return CoachRunRead(
        id=run.id,
        block_id=run.block_id,
        status=run.status,
        model=run.model,
        prompt_version=run.prompt_version,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        latency_ms=run.latency_ms,
        chunks_retrieved=run.chunks_retrieved,
        attempts=run.attempts,
        validation_verdict=run.validation_verdict,
        validation_errors=run.validation_errors,
        proposal=proposal,
        error_detail=run.error_detail,
        created_at=run.created_at,
        updated_at=run.updated_at,
        week=week_read,
    )


# ---------------------------------------------------------------------------
# The calendar policy (Decision 6): a constant of this route, not the engine
# ---------------------------------------------------------------------------


async def _next_week_start_date(
    session: AsyncSession, block: Block, today: date
) -> date:
    """The day after the block's latest session, or today if that is later."""
    latest = await session.execute(
        select(func.max(TrainingSession.date))
        .select_from(TrainingSession)
        .join(TrainingWeek, TrainingSession.week_id == TrainingWeek.id)
        .where(TrainingWeek.block_id == block.id)
    )
    latest_date = latest.scalar_one_or_none()
    if latest_date is None:
        return block.start_date if block.start_date > today else today
    candidate = latest_date + timedelta(days=1)
    return candidate if candidate > today else today


# ---------------------------------------------------------------------------
# Running / resuming the graph
# ---------------------------------------------------------------------------


async def _run_graph(
    initial_or_resume: CoachState | Command[Any],
    *,
    run: AgentRun,
    session: AsyncSession,
    llm: LLMClient,
    embedder: EmbeddingClient,
    checkpointer: BaseCheckpointSaver[Any],
    session_factory: async_sessionmaker[AsyncSession],
    retriever: Retriever,
) -> None:
    """Invoke (or resume) the graph; a failed LLM call ends the run `failed`."""
    graph = coach_graph.build_coach_graph(
        llm=llm,
        embedder=embedder,
        checkpointer=checkpointer,
        session_factory=session_factory,
        retriever=retriever,
    )
    # `RunnableConfig` lives in `langchain_core`, a transitive dependency of
    # `langgraph` we do not import from directly (R61): a plain mapping is
    # structurally what it expects, so the cast is local and typing-only.
    config: dict[str, Any] = {"configurable": {"thread_id": run.thread_id}}
    try:
        await graph.ainvoke(initial_or_resume, config=config)  # type: ignore[call-overload]
    except LLMRequestError as exc:
        run.status = AgentRunStatus.failed
        run.error_detail = str(exc)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM call failed for run {run.id}",
        ) from exc
    await session.refresh(run)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/coach/runs", response_model=CoachRunRead, status_code=status.HTTP_201_CREATED
)
async def create_coach_run(
    payload: CoachRunCreate,
    today: date = Depends(get_today),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client),
    embedder: EmbeddingClient = Depends(get_embedding_client),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    retriever: Retriever = Depends(get_retriever),
) -> CoachRunRead:
    """Propose the next week of an existing block; run the graph to the gate."""
    block = await _owned_block(session, payload.block_id, user.id)

    open_run_result = await session.execute(
        select(AgentRun).where(
            AgentRun.block_id == block.id,
            AgentRun.status == AgentRunStatus.awaiting_gate,
        )
    )
    open_run = open_run_result.scalar_one_or_none()
    if open_run is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"block {block.id} already has an open run: {open_run.id}",
        )

    start_date = await _next_week_start_date(session, block, today)

    run = AgentRun(
        athlete_id=user.id,
        block_id=block.id,
        thread_id=str(uuid4()),
        status=AgentRunStatus.running,
        model=settings.llm_model,
        prompt_version=coach_graph.PROMPT_VERSION,
        validation_verdict="",
        validation_errors=[],
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    initial_state: CoachState = {
        "run_id": run.id,
        "athlete_id": user.id,
        "block_id": block.id,
        "intent": block.intent.value,
        "week_start_date": start_date.isoformat(),
        "attempts": 0,
        "validation_errors": [],
        "validation_verdict": "",
        "chunks_retrieved": 0,
        "used_fallback": False,
    }
    await _run_graph(
        initial_state,
        run=run,
        session=session,
        llm=llm,
        embedder=embedder,
        checkpointer=checkpointer,
        session_factory=session_factory,
        retriever=retriever,
    )

    if run.status == AgentRunStatus.failed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=run.error_detail
        )

    return await _run_read(session, run)


@router.get("/coach/runs/{run_id}", response_model=CoachRunRead)
async def get_coach_run(
    run_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> CoachRunRead:
    """The run, its status, its `created_at`, and its (proposed) week (R39)."""
    run = await _owned_run(session, run_id, user.id)
    return await _run_read(session, run)


async def _resolve_and_resume(
    run_id: int,
    decision: dict[str, Any],
    *,
    user: User,
    session: AsyncSession,
    llm: LLMClient,
    embedder: EmbeddingClient,
    checkpointer: BaseCheckpointSaver[Any],
    session_factory: async_sessionmaker[AsyncSession],
    retriever: Retriever,
) -> CoachRunRead:
    run = await _owned_run(session, run_id, user.id)
    if run.status != AgentRunStatus.awaiting_gate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"run {run.id} is not awaiting a decision",
        )
    await _run_graph(
        Command(resume=decision),
        run=run,
        session=session,
        llm=llm,
        embedder=embedder,
        checkpointer=checkpointer,
        session_factory=session_factory,
        retriever=retriever,
    )
    return await _run_read(session, run)


@router.post("/coach/runs/{run_id}/accept", response_model=CoachRunRead)
async def accept_coach_run(
    run_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client),
    embedder: EmbeddingClient = Depends(get_embedding_client),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    retriever: Retriever = Depends(get_retriever),
) -> CoachRunRead:
    """Activate the proposed week; write `training_weeks.proposal_id` (R47)."""
    return await _resolve_and_resume(
        run_id,
        {"action": "accept"},
        user=user,
        session=session,
        llm=llm,
        embedder=embedder,
        checkpointer=checkpointer,
        session_factory=session_factory,
        retriever=retriever,
    )


@router.post("/coach/runs/{run_id}/edit", response_model=CoachRunRead)
async def edit_coach_run(
    run_id: int,
    payload: CoachEditRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client),
    embedder: EmbeddingClient = Depends(get_embedding_client),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    retriever: Retriever = Depends(get_retriever),
) -> CoachRunRead:
    """Replace the superseded proposal with a revised one.

    Stays `awaiting_gate` (R48).
    """
    return await _resolve_and_resume(
        run_id,
        {"action": "edit", "feedback": payload.feedback},
        user=user,
        session=session,
        llm=llm,
        embedder=embedder,
        checkpointer=checkpointer,
        session_factory=session_factory,
        retriever=retriever,
    )


@router.post("/coach/runs/{run_id}/reject", response_model=CoachRunRead)
async def reject_coach_run(
    run_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    llm: LLMClient = Depends(get_llm_client),
    embedder: EmbeddingClient = Depends(get_embedding_client),
    checkpointer: BaseCheckpointSaver[Any] = Depends(get_checkpointer),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    retriever: Retriever = Depends(get_retriever),
) -> CoachRunRead:
    """Delete the proposed week; activate nothing (R49)."""
    return await _resolve_and_resume(
        run_id,
        {"action": "reject"},
        user=user,
        session=session,
        llm=llm,
        embedder=embedder,
        checkpointer=checkpointer,
        session_factory=session_factory,
        retriever=retriever,
    )
