"""Executed-history helpers shared by `app/blocks.py` and the coach graph.

Moved verbatim out of `app/blocks.py` (D-3): `gather_context` needs exactly
the same executed history the block-status route already shapes for the
engine, and copying the logic would duplicate the one thing that must not
drift. Strictly behaviour-preserving — no new behaviour of any kind (R23).
`resolve_e1rm` is the one addition beyond a straight move: it returns `None`
instead of raising `HTTPException`, because a graph node must not raise HTTP
errors; `app/blocks.py` keeps its 422-with-seed branch by wrapping it.
"""

from datetime import date
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import engine
from app.models import Exercise, TrainingSession, TrainingSet, TrainingWeek

# Data-sufficiency policy (not a training number): with fewer than this many
# executed working sets for a lift we trust the athlete's seed over a thin
# history. yata-0010 ships the route that fills executed_*; until then this
# branch is always the seed.
MIN_TOP_SETS_FOR_E1RM: Final = 3


async def resolve_e1rm(
    session: AsyncSession, athlete_id: int, lift: str
) -> float | None:
    """Resolve a lift's e1RM from recent executed history, or `None`.

    Unlike `app.blocks._e1rm_for_lift`, this raises no `HTTPException` (a
    graph node must not raise HTTP errors) and has no seed fallback: the seed
    is a route-time concept, not something the graph knows about.
    """
    top_sets = await session.execute(
        select(TrainingSet.executed_weight_kg, TrainingSet.executed_reps)
        .join(TrainingSession, TrainingSet.session_id == TrainingSession.id)
        .join(Exercise, TrainingSet.exercise_id == Exercise.id)
        .where(
            TrainingSet.athlete_id == athlete_id,
            TrainingSet.set_type == "working",
            TrainingSet.completed_at.is_not(None),
            TrainingSet.executed_weight_kg.is_not(None),
            TrainingSet.executed_reps.is_not(None),
            Exercise.category == lift,
        )
        .order_by(TrainingSet.completed_at.desc())
        .limit(MIN_TOP_SETS_FOR_E1RM)
    )
    rows = top_sets.all()
    if len(rows) == MIN_TOP_SETS_FOR_E1RM:
        pairs = [(w, r) for w, r in rows if w is not None and r is not None]
        return engine.e1rm_best_of_recent(pairs)
    return None


async def executed_rows(
    session: AsyncSession, block_id: int
) -> list[tuple[TrainingSet, Exercise, TrainingSession]]:
    """Every executed set in the block, joined to its exercise and session."""
    result = await session.execute(
        select(TrainingSet, Exercise, TrainingSession)
        .join(Exercise, TrainingSet.exercise_id == Exercise.id)
        .join(TrainingSession, TrainingSet.session_id == TrainingSession.id)
        .join(TrainingWeek, TrainingSession.week_id == TrainingWeek.id)
        .where(
            TrainingWeek.block_id == block_id,
            TrainingSet.completed_at.is_not(None),
            TrainingSet.executed_weight_kg.is_not(None),
            TrainingSet.executed_reps.is_not(None),
        )
    )
    return [(s, e, sess) for s, e, sess in result.all()]


def hard_sets_by_muscle(
    rows: list[tuple[TrainingSet, Exercise, TrainingSession]],
) -> dict[str, int]:
    """Count executed hard sets per muscle group (engine.is_hard_set decides)."""
    counts: dict[str, int] = {}
    for training_set, exercise, _ in rows:
        intensity = training_set.executed_intensity
        if intensity is None or not engine.is_hard_set(
            training_set.intensity_type.value, intensity
        ):
            continue
        for muscle in exercise.muscle_groups:
            counts[muscle] = counts.get(muscle, 0) + 1
    return counts


def deload_history(
    rows: list[tuple[TrainingSet, Exercise, TrainingSession]],
    overall_zone: engine.VolumeZone,
) -> list[engine.SessionSummary]:
    """One `SessionSummary` per session, keyed by its own best e1RM set.

    Ponytail: a block currently holds a single training week (multi-week
    progression is Phase 3 / the agent gate), so every session shares the
    same `overall_zone` rather than a per-week breakdown.
    """
    by_session: dict[int, list[TrainingSet]] = {}
    session_dates: dict[int, date] = {}
    for training_set, _, training_session in rows:
        by_session.setdefault(training_session.id, []).append(training_set)
        session_dates[training_session.id] = training_session.date

    history = []
    for session_id, sets in by_session.items():
        top_set = max(
            sets,
            key=lambda s: engine.estimate_1rm(
                s.executed_weight_kg or 0.0, s.executed_reps or 1
            ),
        )
        assert top_set.executed_weight_kg is not None
        assert top_set.executed_reps is not None
        assert top_set.executed_intensity is not None
        e1rm_kg = engine.estimate_1rm(top_set.executed_weight_kg, top_set.executed_reps)
        top_rpe = (
            top_set.executed_intensity
            if top_set.intensity_type.value == "RPE"
            else 10.0 - top_set.executed_intensity
        )
        history.append(
            engine.SessionSummary(
                session_date=session_dates[session_id],
                e1rm_kg=e1rm_kg,
                top_set_weight_kg=top_set.executed_weight_kg,
                top_set_reps=top_set.executed_reps,
                top_set_rpe=top_rpe,
                week_volume_zone=overall_zone,
            )
        )
    return history
