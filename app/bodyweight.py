"""Bodyweight routes: upsert a daily log and list the athlete's logs."""

from datetime import date as Date

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import BodyweightLog, User
from app.schemas import BodyweightCreate, BodyweightRead

router = APIRouter()


@router.post(
    "/bodyweight", response_model=BodyweightRead, status_code=status.HTTP_201_CREATED
)
async def create_bodyweight(
    payload: BodyweightCreate,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> BodyweightLog:
    """Create (201) or update (200) the current athlete's log for a date."""
    # SELECT-then-write upsert: portable to SQLite (PG ON CONFLICT is not).

    existing = await session.execute(
        select(BodyweightLog).where(
            BodyweightLog.athlete_id == user.id, BodyweightLog.date == payload.date
        )
    )

    log = existing.scalar_one_or_none()

    if log is not None:
        log.weight_kg = payload.weight_kg
        response.status_code = status.HTTP_200_OK
    else:
        log = BodyweightLog(
            athlete_id=user.id, date=payload.date, weight_kg=payload.weight_kg
        )
        session.add(log)
        response.status_code = status.HTTP_201_CREATED
    await session.commit()
    await session.refresh(log)
    return log


@router.get("/bodyweight", response_model=list[BodyweightRead])
async def list_bodyweight(
    date_from: Date | None = Query(default=None, alias="from"),
    date_to: Date | None = Query(default=None, alias="to"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[BodyweightLog]:
    """List the current athlete's logs ordered by date desc, optionally bounded."""

    stmt = select(BodyweightLog).where(BodyweightLog.athlete_id == user.id)
    if date_from is not None:
        stmt = stmt.where(BodyweightLog.date >= date_from)
    if date_to is not None:
        stmt = stmt.where(BodyweightLog.date <= date_to)

    stmt = stmt.order_by(BodyweightLog.date.desc())

    result = await session.execute(stmt)
    return list(result.scalars().all())
