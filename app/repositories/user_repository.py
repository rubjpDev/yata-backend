"""Async data access for users and their athlete profiles. No business rules."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import AthleteProfile, CompStyle, Discipline, Unit, User


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    """Return the user with the given email, or None if not found."""
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, user_id: int) -> User | None:
    """Return the user with the given id, or None if not found."""
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_by_id_with_profile(session: AsyncSession, user_id: int) -> User | None:
    """Return the user with the given id, eagerly loading its athlete profile."""
    result = await session.execute(
        select(User).options(selectinload(User.profile)).where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def create_user_with_profile(
    session: AsyncSession,
    email: str,
    hashed_password: str,
    display_name: str,
    discipline: Discipline,
    comp_style: CompStyle,
    unit: Unit,
    equipment_owned: dict[str, bool],
) -> User:
    """Insert a user row and its athlete profile row in one transaction."""
    user = User(
        email=email,
        hashed_password=hashed_password,
        display_name=display_name,
    )
    session.add(user)
    await session.flush()

    session.add(
        AthleteProfile(
            user_id=user.id,
            discipline=discipline,
            comp_style=comp_style,
            unit=unit,
            equipment_owned=equipment_owned,
        )
    )

    await session.commit()

    created = await get_by_id_with_profile(session, user.id)
    if created is None:  # pragma: no cover - defensive: just-committed row
        raise RuntimeError("Failed to load the just-created user with its profile")
    return created
