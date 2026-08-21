"""Athlete profile routes: read and partially update the athlete fields on `User`."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import ProfileRead, ProfileUpdate

router = APIRouter()


@router.get("/profile", response_model=ProfileRead)
async def get_profile(user: User = Depends(get_current_user)) -> User:
    """Return the authenticated athlete's mutable training-profile fields."""
    return user


@router.patch("/profile", response_model=ProfileRead)
async def update_profile(
    payload: ProfileUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Apply only the fields present in the payload (partial update)."""
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(user, field, value)

    await session.commit()
    await session.refresh(user)
    return user
