"""Training session routes and set-execution: the athlete's execution path."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, get_today
from app.models import SessionStatus, TrainingSession, TrainingSet, User
from app.schemas import SessionRead, SetExecutionUpdate, SetRead

router = APIRouter()


def _session_read(session_row: TrainingSession, sets: list[TrainingSet]) -> SessionRead:
    """Shape a `TrainingSession` + its sets into `SessionRead` (no ORM relationship)."""
    return SessionRead(
        id=session_row.id,
        week_id=session_row.week_id,
        date=session_row.date,
        session_type=session_row.session_type,
        status=session_row.status,
        created_at=session_row.created_at,
        sets=[SetRead.model_validate(s) for s in sets],
    )


async def _sets_for_session(db: AsyncSession, session_id: int) -> list[TrainingSet]:
    result = await db.execute(
        select(TrainingSet)
        .where(TrainingSet.session_id == session_id)
        .order_by(TrainingSet.set_order)
    )
    return list(result.scalars().all())


def _derive_session_status(sets: list[TrainingSet]) -> SessionStatus:
    """No executed sets -> prescribed; some -> partial; all -> completed.

    A set counts as executed when `completed_at` is set (same signal already
    used by `app.blocks._e1rm_for_lift`).
    """
    executed_count = sum(1 for s in sets if s.completed_at is not None)
    if executed_count == 0:
        return SessionStatus.prescribed
    if executed_count == len(sets):
        return SessionStatus.completed
    return SessionStatus.partial


@router.get("/sessions/today", response_model=SessionRead)
async def get_today_session(
    today: date = Depends(get_today),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionRead:
    """The athlete's session scheduled for `today`; 404 if none."""
    result = await db.execute(
        select(TrainingSession).where(
            TrainingSession.athlete_id == user.id, TrainingSession.date == today
        )
    )
    session_row = result.scalar_one_or_none()
    if session_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No session scheduled today"
        )
    sets = await _sets_for_session(db, session_row.id)
    return _session_read(session_row, sets)


@router.get("/sessions/{session_id}", response_model=SessionRead)
async def get_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionRead:
    """A session with its prescribed-vs-executed sets; not-yours is 404."""
    result = await db.execute(
        select(TrainingSession).where(
            TrainingSession.id == session_id, TrainingSession.athlete_id == user.id
        )
    )
    session_row = result.scalar_one_or_none()
    if session_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    sets = await _sets_for_session(db, session_row.id)
    return _session_read(session_row, sets)


@router.patch("/sets/{set_id}/execution", response_model=SetRead)
async def update_set_execution(
    set_id: int,
    payload: SetExecutionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SetRead:
    """Record what the athlete actually did for one set; re-derives session status.

    The client never sends `status`: it is recomputed here from the session's
    sets after every PATCH (see `_derive_session_status`).
    """
    result = await db.execute(
        select(TrainingSet).where(
            TrainingSet.id == set_id, TrainingSet.athlete_id == user.id
        )
    )
    set_row = result.scalar_one_or_none()
    if set_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Set not found"
        )

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(set_row, field, value)
    await db.flush()

    sets = await _sets_for_session(db, set_row.session_id)
    session_result = await db.execute(
        select(TrainingSession).where(TrainingSession.id == set_row.session_id)
    )
    training_session = session_result.scalar_one()
    training_session.status = _derive_session_status(sets)

    await db.commit()
    await db.refresh(set_row)
    return SetRead.model_validate(set_row)
