"""Exercise catalog routes: create custom exercises and list visible ones."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import Exercise, ExerciseCategory, User
from app.schemas import ExerciseCreate, ExerciseRead

router = APIRouter()


@router.post(
    "/exercises", response_model=ExerciseRead, status_code=status.HTTP_201_CREATED
)
async def create_exercise(
    payload: ExerciseCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Exercise:
    """Create a custom exercise owned by the current user."""
    # Reject a duplicate of one of the user's own custom names (the DB also
    # guards this with UNIQUE(created_by, name); we answer 409 instead of 500).

    duplicate = await session.execute(
        select(Exercise.id).where(
            Exercise.created_by == user.id, Exercise.name == payload.name
        )
    )

    if duplicate.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a custom exercise with this name",
        )

    # created_by is always the current user: this route never creates system
    # exercises (acceptance #5).

    exercise = Exercise(
        name=payload.name,
        category=payload.category,
        muscle_groups=payload.muscle_groups,
        created_by=user.id,
    )

    session.add(exercise)
    await session.commit()
    await session.refresh(exercise)
    return exercise


@router.get("/exercises", response_model=list[ExerciseRead])
async def list_exercises(
    category: ExerciseCategory | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[Exercise]:
    """List system exercises (created_by NULL) plus the current user's customs."""

    stmt = select(Exercise).where(
        or_(Exercise.created_by.is_(None), Exercise.created_by == user.id)
    )

    if category is not None:
        stmt = stmt.where(Exercise.category == category)
    stmt = stmt.order_by(Exercise.id)

    result = await session.execute(stmt)
    return list(result.scalars().all())
