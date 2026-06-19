"""Async data access for users (a single-table athlete). No business rules."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import CompStyle, Discipline, Unit, User


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    """Return the user with the given email, or None if not found."""
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, user_id: int) -> User | None:
    """Return the user with the given id, or None if not found."""
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    email: str,
    hashed_password: str,
    display_name: str,
    discipline: Discipline,
    comp_style: CompStyle,
    unit: Unit,
    equipment_owned: dict[str, bool],
) -> User:
    """Insert a single user row carrying identity and athlete fields."""
    user = User(
        email=email,
        hashed_password=hashed_password,
        display_name=display_name,
        discipline=discipline,
        comp_style=comp_style,
        unit=unit,
        equipment_owned=equipment_owned,
    )
    session.add(user)
    await session.commit()
    return user
