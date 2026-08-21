"""The coach graph: state, nodes, prompts, `build_coach_graph()`.

The one rule: the agent never invents a number. `propose_week` (the LLM call)
emits STRUCTURE ONLY — `intent`, `days`, per-session `equipment_config` and a
`rationale` (`app.schemas.WeekProposal`, D-4). Every kilo, rep, RPE and volume
figure comes from `engine.prescribe_week`, called in `compute_numbers` with no
arithmetic performed on its result (R36); `app/coach.py` copies its output
field by field into `TrainingSet` rows, exactly as `app/blocks.py` already
does for the human-authored path (R52).

State holds only JSON-serializable values (R27): it is serialized into the
checkpoint, and the pause of `human_gate` (R37) must survive a process
restart. Nodes that need the database open their own short session from an
injected `session_factory`; nothing that needs `interrupt()` may re-run a
non-idempotent write, because LangGraph replays a node from its start on
resume (see `_human_gate`, which materializes the proposed week only once by
checking for it before inserting).
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import date
from functools import partial
from typing import Any, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import engine, rag
from app.embeddings import EmbeddingClient
from app.llm import LLMClient
from app.models import (
    AgentRun,
    AgentRunStatus,
    Exercise,
    KnowledgeChunk,
    TrainingSession,
    TrainingSet,
    TrainingWeek,
    WeekStatus,
)
from app.schemas import ProposedSession, WeekProposal

PROMPT_VERSION = "v1"

# The three main lifts every intent's template uses (see `app/engine.py`):
# resolvable upfront in `gather_context`, before the agent has chosen `intent`.
_MAIN_LIFTS: tuple[str, ...] = ("squat", "bench", "deadlift")

_SYSTEM_PROMPT = (
    "You are a powerlifting coach assistant proposing next week's training "
    "block. You decide ONLY four things: the training intent (one of "
    "accumulation, intensification, peak, deload, general), how many "
    "sessions it runs (1-4), which of the athlete's OWNED equipment is used "
    "in each session, and a short rationale grounded in the coaching notes "
    "you are given. You NEVER decide a weight, a rep count, an RPE, a "
    "percentage or a volume figure of any kind — a separate deterministic "
    "system computes every one of those from your intent and day count. "
    "Reply with ONLY a JSON object, no markdown fences, no prose, shaped "
    'exactly like: {"intent": "accumulation", "days": 3, "sessions": '
    '[{"equipment_config": {"belt": false, "knee_sleeves": false, '
    '"knee_wraps": false, "wrist_wraps": false}}], "rationale": "..."} '
    "— one `sessions` entry per day."
)


class Retriever(Protocol):
    """The exact shape of `app.rag.retrieve` (R26): never changed, never widened."""

    async def __call__(
        self,
        session: AsyncSession,
        query: str,
        embedder: EmbeddingClient,
        *,
        topic: str | None = None,
        k: int = 5,
    ) -> list[KnowledgeChunk]: ...


class CoachState(TypedDict, total=False):
    """Everything the graph carries between nodes. JSON-serializable only (R27)."""

    run_id: int
    athlete_id: int
    block_id: int
    week_index: int
    week_start_date: str
    intent: str
    previous_days_planned: int
    deload_signal: bool
    lift_muscle_groups: dict[str, list[str]]
    equipment_owned: dict[str, bool]
    e1rm_by_lift: dict[str, float]
    missing_lift: str | None
    retrieved_chunks: list[str]
    chunks_retrieved: int
    attempts: int
    validation_errors: list[str]
    validation_verdict: str
    proposal: dict[str, Any] | None
    prescribed_sessions: list[dict[str, Any]] | None
    model: str
    prompt_version: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int | None
    used_fallback: bool
    feedback: str | None
    decision: dict[str, Any] | None
    outcome: str | None


SessionFactory = async_sessionmaker[AsyncSession]


# ---------------------------------------------------------------------------
# gather_context
# ---------------------------------------------------------------------------


async def _gather_context(
    state: CoachState, *, session_factory: SessionFactory
) -> dict[str, Any]:
    """Read everything the graph needs; make no LLM call (R21)."""
    from app import history  # local import: keeps app.history free of app.graph

    async with session_factory() as session:
        block_result = await session.execute(
            select(TrainingWeek)
            .where(TrainingWeek.block_id == state["block_id"])
            .order_by(TrainingWeek.week_index.desc())
        )
        weeks = list(block_result.scalars().all())
        latest_week = weeks[0] if weeks else None
        next_week_index = (latest_week.week_index + 1) if latest_week else 1
        previous_days_planned = latest_week.days_planned if latest_week else 1

        rows = await history.executed_rows(session, state["block_id"])
        hard_sets = history.hard_sets_by_muscle(rows)
        zones = engine.weekly_volume_status(hard_sets) if hard_sets else {}
        overall_zone = engine.worst_zone(zones.values()) if zones else "below_MEV"
        deload_history = history.deload_history(rows, overall_zone)
        deload_signal = bool(
            deload_history and engine.should_deload(deload_history, date.today())[0]
        )

        e1rm_by_lift: dict[str, float] = {}
        missing_lift: str | None = None
        for lift in _MAIN_LIFTS:
            value = await history.resolve_e1rm(session, state["athlete_id"], lift)
            if value is None:
                missing_lift = lift
                break
            e1rm_by_lift[lift] = value

        exercise_result = await session.execute(
            select(Exercise.category, Exercise.muscle_groups).where(
                Exercise.created_by.is_(None), Exercise.category.in_(_MAIN_LIFTS)
            )
        )
        lift_muscle_groups = {
            category: list(groups) for category, groups in exercise_result.all()
        }

        if missing_lift is not None:
            run = await session.get(AgentRun, state["run_id"])
            if run is not None:
                run.status = AgentRunStatus.failed
                run.error_detail = f"no 1RM available for {missing_lift}"
                await session.commit()

    update: dict[str, Any] = {
        "week_index": next_week_index,
        "previous_days_planned": previous_days_planned,
        "deload_signal": deload_signal,
        "e1rm_by_lift": e1rm_by_lift,
        "lift_muscle_groups": lift_muscle_groups,
        "missing_lift": missing_lift,
    }
    return update


def _route_after_gather(state: CoachState) -> str:
    return END if state.get("missing_lift") else "retrieve"


# ---------------------------------------------------------------------------
# retrieve
# ---------------------------------------------------------------------------


async def _retrieve(
    state: CoachState,
    *,
    embedder: EmbeddingClient,
    retriever: Retriever,
    session_factory: SessionFactory,
) -> dict[str, Any]:
    """Query deterministically from gathered context; no LLM call (R26)."""
    query = (
        f"weekly programming for a {state['intent']} block, "
        f"deload signal={state.get('deload_signal', False)}"
    )
    async with session_factory() as session:
        chunks = await retriever(session, query, embedder, k=5)
    texts = [chunk.content for chunk in chunks]
    return {"retrieved_chunks": texts, "chunks_retrieved": len(texts)}


# ---------------------------------------------------------------------------
# propose_week / revise — the only two nodes that call the LLM
# ---------------------------------------------------------------------------


def _user_prompt(state: CoachState, *, extra: str | None) -> str:
    lines = [
        f"Current block intent: {state.get('intent')}",
        f"Deload signal fired: {state.get('deload_signal', False)}",
        f"Athlete owns this equipment: {state.get('equipment_owned', {})}",
        "Coaching notes retrieved for this cycle:",
        *[f"- {chunk}" for chunk in state.get("retrieved_chunks", [])],
    ]
    if extra:
        lines.append(f"Fix this before replying again: {extra}")
    return "\n".join(lines)


def _strip_fences(text: str) -> str:
    """Drop a wrapping ```json ... ``` fence some models add despite instructions."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.endswith("```"):
            stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _consume_llm_response(
    response_text: str,
    *,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    latency_ms: int,
) -> dict[str, Any]:
    """Parse an LLM completion into `WeekProposal`, or record why it failed."""
    update: dict[str, Any] = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": latency_ms,
    }
    try:
        proposal = WeekProposal.model_validate_json(_strip_fences(response_text))
    except ValidationError as exc:
        update["proposal"] = None
        update["validation_errors"] = [str(exc)]
        return update
    update["proposal"] = proposal.model_dump(mode="json")
    update["validation_errors"] = []
    return update


async def _propose_week(state: CoachState, *, llm: LLMClient) -> dict[str, Any]:
    response = await llm.complete(
        system=_SYSTEM_PROMPT, user=_user_prompt(state, extra=None)
    )
    update = _consume_llm_response(
        response.text,
        model=response.model,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        latency_ms=response.latency_ms,
    )
    if update["proposal"] is None:
        update["attempts"] = state.get("attempts", 0) + 1
    return update


async def _revise(state: CoachState, *, llm: LLMClient) -> dict[str, Any]:
    """Re-enter with either the athlete's edit feedback or the validation errors.

    One node covers both D11-3 cases (R31, R48): whichever is present in state
    is what goes into the prompt.
    """
    extra = state.get("feedback") or "; ".join(state.get("validation_errors") or [])
    response = await llm.complete(
        system=_SYSTEM_PROMPT, user=_user_prompt(state, extra=extra)
    )
    update = _consume_llm_response(
        response.text,
        model=response.model,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        latency_ms=response.latency_ms,
    )
    update["feedback"] = None
    if update["proposal"] is None:
        update["attempts"] = state.get("attempts", 0) + 1
    return update


def _route_after_propose(state: CoachState) -> str:
    if state.get("proposal") is not None:
        return "compute_numbers"
    return _next_attempt_route(state.get("attempts", 0))


def _next_attempt_route(attempts: int) -> str:
    """R30/R31's one counter, shared by every failure kind."""
    if attempts < 2:
        return "propose_week"
    if attempts < 3:
        return "revise"
    return "fallback_template"


# ---------------------------------------------------------------------------
# fallback_template — no LLM call at all (R32)
# ---------------------------------------------------------------------------


def _fallback_template(state: CoachState) -> dict[str, Any]:
    intent = "deload" if state.get("deload_signal") else state.get("intent", "general")
    days = max(1, min(4, state.get("previous_days_planned", 1)))
    proposal = WeekProposal(
        intent=intent,  # type: ignore[arg-type]
        days=days,
        sessions=[ProposedSession() for _ in range(days)],
        rationale="fallback template: engine defaults after repeated invalid proposals",
    )
    return {
        "proposal": proposal.model_dump(mode="json"),
        "used_fallback": True,
        "validation_verdict": "fallback_template",
    }


# ---------------------------------------------------------------------------
# compute_numbers — engine.prescribe_week, no arithmetic on its result (R36)
# ---------------------------------------------------------------------------


def _compute_numbers(state: CoachState) -> dict[str, Any]:
    proposal = state.get("proposal")
    if proposal is None:
        return {"prescribed_sessions": None}
    try:
        prescribed = engine.prescribe_week(
            proposal["intent"],
            proposal["days"],
            state.get("e1rm_by_lift", {}),
            state["week_index"],
        )
    except ValueError:
        return {"prescribed_sessions": None}
    return {"prescribed_sessions": [asdict(session) for session in prescribed]}


# ---------------------------------------------------------------------------
# validate_week — pure (R33): no database, no network, no LLM
# ---------------------------------------------------------------------------


def validate_week(
    *,
    intent: str,
    days: int,
    e1rm_by_lift: Mapping[str, float],
    week_index: int,
    lift_muscle_groups: Mapping[str, Sequence[str]],
    equipment_owned: Mapping[str, bool],
    sessions_equipment_config: Sequence[Mapping[str, bool]],
    deload_signal: bool,
) -> list[str]:
    """Every violated rule, empty means valid. Plain data in and out (R33)."""
    violations: list[str] = []

    try:
        prescribed = engine.prescribe_week(intent, days, e1rm_by_lift, week_index)
    except ValueError as exc:
        violations.append(str(exc))
        prescribed = ()

    if prescribed:
        hard_sets: dict[str, int] = {}
        for prescribed_session in prescribed:
            for prescribed_set in prescribed_session.sets:
                if not engine.is_hard_set(
                    prescribed_set.intensity_type, prescribed_set.intensity
                ):
                    continue
                for muscle in lift_muscle_groups.get(prescribed_set.lift, []):
                    hard_sets[muscle] = hard_sets.get(muscle, 0) + 1
        if hard_sets:
            for muscle, zone in engine.weekly_volume_status(hard_sets).items():
                if zone == "above_MRV":
                    violations.append(f"{muscle} lands above_MRV at this volume")

    if deload_signal and intent != "deload":
        violations.append("deload signal fired but the proposed intent is not deload")

    for session_config in sessions_equipment_config:
        for gear, used in session_config.items():
            if used and not equipment_owned.get(gear, False):
                violations.append(f"equipment {gear!r} is not owned by the athlete")

    return violations


def _validate_week_node(state: CoachState) -> dict[str, Any]:
    proposal = state.get("proposal")
    if proposal is None:
        violations = ["no proposal to validate"]
    else:
        violations = validate_week(
            intent=proposal["intent"],
            days=proposal["days"],
            e1rm_by_lift=state.get("e1rm_by_lift", {}),
            week_index=state["week_index"],
            lift_muscle_groups=state.get("lift_muscle_groups", {}),
            equipment_owned=state.get("equipment_owned", {}),
            sessions_equipment_config=[
                session["equipment_config"] for session in proposal["sessions"]
            ],
            deload_signal=state.get("deload_signal", False),
        )

    if state.get("used_fallback"):
        return {"validation_errors": violations}

    if violations:
        return {
            "validation_errors": violations,
            "attempts": state.get("attempts", 0) + 1,
            "validation_verdict": "invalid",
        }
    return {"validation_errors": [], "validation_verdict": "valid"}


def _route_after_validate(state: CoachState) -> str:
    if state.get("used_fallback") or not state.get("validation_errors"):
        return "human_gate"
    return _next_attempt_route(state.get("attempts", 0))


# ---------------------------------------------------------------------------
# human_gate — interrupt() + the one materialization of the proposed week
# ---------------------------------------------------------------------------


async def _update_run_metrics(session: AsyncSession, state: CoachState) -> None:
    """Idempotent UPDATE of the one `agent_runs` row for this thread (R42)."""
    run = await session.get(AgentRun, state["run_id"])
    if run is None:
        return
    run.status = AgentRunStatus.awaiting_gate
    run.model = state.get("model") or run.model
    run.prompt_version = state.get("prompt_version") or run.prompt_version
    run.prompt_tokens = state.get("prompt_tokens")
    run.completion_tokens = state.get("completion_tokens")
    run.latency_ms = state.get("latency_ms")
    run.chunks_retrieved = state.get("chunks_retrieved", 0)
    run.attempts = state.get("attempts", 0)
    run.validation_verdict = state.get("validation_verdict", "valid")
    run.validation_errors = state.get("validation_errors", [])
    run.proposal = state.get("proposal")
    await session.commit()


async def _human_gate(
    state: CoachState, *, session_factory: SessionFactory
) -> dict[str, Any]:
    # Local import: `app.coach` owns the field-by-field assignment from a
    # `PrescribedSet` into a `TrainingSet` (R52's traceability grep expects
    # those lines in app/coach.py, mirroring app/blocks.py::create_block).
    # Importing it at module scope would cycle, since app.coach imports
    # `build_coach_graph` from this module.
    from app import coach

    async with session_factory() as session:
        week_id = await coach.materialize_proposed_week(session, state)
        await _update_run_metrics(session, state)

    decision = interrupt(
        {"run_id": state["run_id"], "week_id": week_id, "proposal": state["proposal"]}
    )
    return {"decision": decision}


# ---------------------------------------------------------------------------
# apply_decision — accept / edit / reject, one transaction each (R47-R49)
# ---------------------------------------------------------------------------


async def _delete_week(session: AsyncSession, week: TrainingWeek) -> None:
    """Delete a `proposed` week with its sessions and sets (R48, R49).

    Never called on anything but `status = proposed` — the caller only looks
    up weeks filtered that way, so an `active` week is never in reach here.
    """
    session_rows = await session.execute(
        select(TrainingSession.id).where(TrainingSession.week_id == week.id)
    )
    session_ids = [row[0] for row in session_rows.all()]
    if session_ids:
        await session.execute(
            delete(TrainingSet).where(TrainingSet.session_id.in_(session_ids))
        )
        await session.execute(
            delete(TrainingSession).where(TrainingSession.week_id == week.id)
        )
    await session.delete(week)


async def _apply_decision(
    state: CoachState, *, session_factory: SessionFactory
) -> dict[str, Any]:
    decision = state.get("decision") or {}
    action = decision.get("action")

    async with session_factory() as session:
        run = await session.get(AgentRun, state["run_id"])
        week_result = await session.execute(
            select(TrainingWeek).where(
                TrainingWeek.block_id == state["block_id"],
                TrainingWeek.week_index == state["week_index"],
                TrainingWeek.status == WeekStatus.proposed,
            )
        )
        week = week_result.scalar_one_or_none()

        if action == "accept":
            if week is not None and run is not None:
                week.status = WeekStatus.active
                week.proposal_id = run.id
            if run is not None:
                run.status = AgentRunStatus.accepted
            await session.commit()
            return {"outcome": "accepted"}

        if action == "reject":
            if week is not None:
                await _delete_week(session, week)
            if run is not None:
                run.status = AgentRunStatus.rejected
            await session.commit()
            return {"outcome": "rejected"}

        if action == "edit":
            if week is not None:
                await _delete_week(session, week)
            await session.commit()
            return {"outcome": "edit", "feedback": decision.get("feedback", "")}

    return {"outcome": "unknown"}


def _route_after_decision(state: CoachState) -> str:
    return "revise" if state.get("outcome") == "edit" else END


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


def build_coach_graph(
    *,
    llm: LLMClient,
    embedder: EmbeddingClient,
    checkpointer: BaseCheckpointSaver[Any],
    session_factory: SessionFactory,
    retriever: Retriever = rag.retrieve,
) -> CompiledStateGraph[CoachState, Any, CoachState, CoachState]:
    """Wire the nine nodes of the coach graph (R35) and compile with `checkpointer`.

    `retriever` is a default keyword argument, not a `Protocol` implemented by
    a second class: without it every offline test would need PostgreSQL,
    because `retrieve` computes cosine distance in the database.
    """
    builder: StateGraph[CoachState, Any, CoachState, CoachState] = StateGraph(
        CoachState
    )

    builder.add_node(
        "gather_context", partial(_gather_context, session_factory=session_factory)
    )
    builder.add_node(
        "retrieve",
        partial(
            _retrieve,
            embedder=embedder,
            retriever=retriever,
            session_factory=session_factory,
        ),
    )
    builder.add_node("propose_week", partial(_propose_week, llm=llm))
    builder.add_node("compute_numbers", _compute_numbers)
    builder.add_node("validate_week", _validate_week_node)
    builder.add_node("revise", partial(_revise, llm=llm))
    builder.add_node("fallback_template", _fallback_template)
    builder.add_node(
        "human_gate", partial(_human_gate, session_factory=session_factory)
    )
    builder.add_node(
        "apply_decision", partial(_apply_decision, session_factory=session_factory)
    )

    builder.add_edge(START, "gather_context")
    builder.add_conditional_edges(
        "gather_context", _route_after_gather, {"retrieve": "retrieve", END: END}
    )
    builder.add_edge("retrieve", "propose_week")
    builder.add_conditional_edges(
        "propose_week",
        _route_after_propose,
        {
            "compute_numbers": "compute_numbers",
            "propose_week": "propose_week",
            "revise": "revise",
            "fallback_template": "fallback_template",
        },
    )
    builder.add_edge("compute_numbers", "validate_week")
    builder.add_conditional_edges(
        "validate_week",
        _route_after_validate,
        {
            "human_gate": "human_gate",
            "propose_week": "propose_week",
            "revise": "revise",
            "fallback_template": "fallback_template",
        },
    )
    builder.add_conditional_edges(
        "revise",
        _route_after_propose,
        {
            "compute_numbers": "compute_numbers",
            "propose_week": "propose_week",
            "revise": "revise",
            "fallback_template": "fallback_template",
        },
    )
    builder.add_edge("fallback_template", "compute_numbers")
    builder.add_edge("human_gate", "apply_decision")
    builder.add_conditional_edges(
        "apply_decision", _route_after_decision, {"revise": "revise", END: END}
    )

    return builder.compile(checkpointer=checkpointer)
