"""Async data access for users and their role profiles. No business rules."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role, TraineeProfile, TrainerProfile, User


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    """Return the user with the given email, or None if not found."""
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, user_id: int) -> User | None:
    """Return the user with the given id, or None if not found."""
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user_with_profile(
    session: AsyncSession,
    email: str,
    hashed_password: str,
    role: Role,
    display_name: str,
) -> User:
    """Insert a user row and its matching role profile row in one transaction."""
    user = User(
        email=email,
        hashed_password=hashed_password,
        role=role,
        display_name=display_name,
    )
    session.add(user)
    await session.flush()

    if role is Role.trainer:
        session.add(TrainerProfile(user_id=user.id))
    else:
        session.add(TraineeProfile(user_id=user.id))

    await session.commit()
    await session.refresh(user)
    return user
