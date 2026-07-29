"""Training block routes: create block+week1+sessions+sets, and read a block."""

from datetime import timedelta
from typing import Final

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import engine
from app.db import get_db
from app.deps import get_current_user
from app.models import (
    Block,
    Exercise,
    TrainingSession,
    TrainingSet,
    TrainingWeek,
    User,
    WeekStatus,
)
from app.schemas import BlockCreate, BlockRead, WeekRead

router = APIRouter()


def _block_read(block: Block, weeks: list[TrainingWeek]) -> BlockRead:
    """Shape a `Block` + its weeks into `BlockRead` (no ORM relationship, D-11)."""
    return BlockRead(
        id=block.id,
        athlete_id=block.athlete_id,
        intent=block.intent,
        planned_weeks=block.planned_weeks,
        start_date=block.start_date,
        status=block.status,
        created_at=block.created_at,
        weeks=[WeekRead.model_validate(week) for week in weeks],
    )


# Data-sufficiency policy (not a training number): with fewer than this many
# executed working sets for a lift we trust the athlete's seed over a thin
# history. yata-0010 ships the route that fills executed_*; until then this
# branch is always the seed.
MIN_TOP_SETS_FOR_E1RM: Final = 3


async def _system_exercise_ids(session: AsyncSession) -> dict[str, int]:
    """Map each main-lift category to its system exercise id (created_by NULL)."""
    result = await session.execute(
        select(Exercise.id, Exercise.category).where(Exercise.created_by.is_(None))
    )
    return {category: exercise_id for exercise_id, category in result.all()}


async def _e1rm_for_lift(
    session: AsyncSession, athlete_id: int, lift: str, seed_1rm_kg: dict[str, float]
) -> float:
    """Resolve a lift's e1RM from recent executed history, else the seed (D-7)."""
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
    if lift in seed_1rm_kg:
        return seed_1rm_kg[lift]
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"no 1RM available for {lift}: not enough executed history and no seed",
    )


@router.post("/blocks", response_model=BlockRead, status_code=status.HTTP_201_CREATED)
async def create_block(
    payload: BlockCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> BlockRead:
    """Create a block + its first training week, prescribed by the engine.

    Every load/rep/RPE number comes from `engine.prescribe_week`; this route
    only resolves lifts to exercises, resolves e1RMs, and assigns.
    """
    exercise_ids = await _system_exercise_ids(session)

    needed_lifts = engine.lifts_needed(payload.intent.value, payload.days)

    missing_exercise = next(
        (lift for lift in needed_lifts if lift not in exercise_ids), None
    )
    if missing_exercise is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"no system exercise for lift {missing_exercise!r}",
        )

    e1rm_by_lift = {
        lift: await _e1rm_for_lift(session, user.id, lift, payload.seed_1rm_kg)
        for lift in needed_lifts
    }

    prescribed_sessions = engine.prescribe_week(
        payload.intent.value, payload.days, e1rm_by_lift, week_index=1
    )

    block = Block(
        athlete_id=user.id,
        intent=payload.intent,
        planned_weeks=payload.planned_weeks,
        start_date=payload.start_date,
    )
    session.add(block)
    await session.flush()

    week = TrainingWeek(
        block_id=block.id,
        athlete_id=user.id,
        week_index=1,
        days_planned=payload.days,
        status=WeekStatus.active,
    )
    session.add(week)
    await session.flush()

    for prescribed_session in prescribed_sessions:
        training_session = TrainingSession(
            week_id=week.id,
            athlete_id=user.id,
            date=payload.start_date + timedelta(days=prescribed_session.day_offset),
            session_type=prescribed_session.session_type,
        )
        session.add(training_session)
        await session.flush()

        for prescribed_set in prescribed_session.sets:
            session.add(
                TrainingSet(
                    session_id=training_session.id,
                    athlete_id=user.id,
                    exercise_id=exercise_ids[prescribed_set.lift],
                    set_order=prescribed_set.set_order,
                    set_type=prescribed_set.set_type,
                    intensity_type=prescribed_set.intensity_type,
                    weight_mode=prescribed_set.weight_mode,
                    equipment_config={},
                    prescribed_weight_kg=prescribed_set.weight_kg,
                    prescribed_reps=prescribed_set.reps,
                    prescribed_intensity=prescribed_set.intensity,
                    executed_weight_kg=None,
                    executed_reps=None,
                    executed_intensity=None,
                    completed_at=None,
                )
            )

    await session.commit()
    await session.refresh(block)
    await session.refresh(week)
    return _block_read(block, [week])


@router.get("/blocks/{block_id}", response_model=BlockRead)
async def get_block(
    block_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> BlockRead:
    """Fetch a block with its training weeks; not-yours and not-there both 404."""
    result = await session.execute(
        select(Block).where(Block.id == block_id, Block.athlete_id == user.id)
    )
    block = result.scalar_one_or_none()
    if block is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Block not found"
        )

    weeks_result = await session.execute(
        select(TrainingWeek)
        .where(TrainingWeek.block_id == block.id)
        .order_by(TrainingWeek.week_index)
    )
    weeks = list(weeks_result.scalars().all())

    return _block_read(block, weeks)
