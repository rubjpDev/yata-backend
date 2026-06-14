"""User, role, and profile ORM models."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Role(StrEnum):
    """User roles supported by the platform."""

    trainer = "trainer"
    trainee = "trainee"


class User(Base):
    """A registered account, either a trainer or a trainee."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role, name="user_role"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TrainerProfile(Base):
    """Placeholder profile table for trainers."""

    __tablename__ = "trainer_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)


class TraineeProfile(Base):
    """Placeholder profile table for trainees."""

    __tablename__ = "trainee_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
