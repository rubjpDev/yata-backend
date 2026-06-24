"""All SQLAlchemy ORM models and enums."""

from datetime import date, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

_DEFAULT_EQUIPMENT_OWNED: dict[str, bool] = {
    "belt": False,
    "knee_sleeves": False,
    "knee_wraps": False,
    "wrist_wraps": False,
}


class Discipline(StrEnum):
    """Training disciplines supported by the platform."""

    powerlifting = "powerlifting"


class Unit(StrEnum):
    """Load units an athlete may train in."""

    kg = "kg"
    lb = "lb"


class CompStyle(StrEnum):
    """Competition styles an athlete may compete under."""

    raw = "raw"
    classic = "classic"
    equipped = "equipped"


class ExerciseCategory(StrEnum):
    """Exercise catalog categories."""

    squat = "squat"
    bench = "bench"
    deadlift = "deadlift"
    accessory = "accessory"


class User(Base):
    """A registered athlete account: identity + athlete fields, single table."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.false()
    )
    discipline: Mapped[Discipline] = mapped_column(
        Enum(Discipline, name="discipline"), nullable=False
    )
    unit: Mapped[Unit] = mapped_column(
        Enum(Unit, name="unit"),
        nullable=False,
        server_default=Unit.kg.value,
    )
    comp_style: Mapped[CompStyle] = mapped_column(
        Enum(CompStyle, name="comp_style"),
        nullable=False,
        server_default=CompStyle.classic.value,
    )
    equipment_owned: Mapped[dict[str, bool]] = mapped_column(
        JSON, nullable=False, default=dict(_DEFAULT_EQUIPMENT_OWNED)
    )
    training_days_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Exercise(Base):
    """A catalog exercise: either system-wide (created_by NULL) or a user custom."""

    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[ExerciseCategory] = mapped_column(
        Enum(ExerciseCategory, name="exercise_category"), nullable=False
    )
    muscle_groups: Mapped[list[str]] = mapped_column(
        JSON().with_variant(ARRAY(String), "postgresql"),
        nullable=False,
        default=list,
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BodyweightLog(Base):
    """A single bodyweight log entry for an athlete."""

    __tablename__ = "bodyweight_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
