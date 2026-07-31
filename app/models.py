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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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


class BlockIntent(StrEnum):
    """Training intent driving a block's weekly template."""

    accumulation = "accumulation"
    intensification = "intensification"
    peak = "peak"
    deload = "deload"
    general = "general"


class BlockStatus(StrEnum):
    """Lifecycle status of a training block."""

    active = "active"
    completed = "completed"
    abandoned = "abandoned"


class WeekStatus(StrEnum):
    """Lifecycle status of a training week.

    `proposed` is reserved for the Phase-3 agent gate (ADR-010); this feature
    only ever creates `active` weeks.
    """

    proposed = "proposed"
    active = "active"
    completed = "completed"


class SessionType(StrEnum):
    """Kind of training session within a week."""

    squat = "squat"
    bench = "bench"
    deadlift = "deadlift"
    # Named `upper_body`/`lower_body`, not `upper`/`lower`: those names would
    # shadow `str.upper`/`str.lower` on a StrEnum member.
    upper_body = "upper"
    lower_body = "lower"
    full = "full"


class SessionStatus(StrEnum):
    """Derived completion status of a training session.

    Ships now so yata-0010 (light lane) does not need its own migration; this
    feature only ever creates `prescribed` sessions.
    """

    prescribed = "prescribed"
    partial = "partial"
    completed = "completed"


class SetType(StrEnum):
    """Role of a prescribed set within a session."""

    warmup = "warmup"
    working = "working"
    backoff = "backoff"


class IntensityType(StrEnum):
    """Unit the prescribed/executed intensity is expressed in."""

    RPE = "RPE"
    RIR = "RIR"


class WeightMode(StrEnum):
    """Whether a set's load is a fixed prescription or athlete-chosen."""

    fixed = "fixed"
    free = "free"


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


class Block(Base):
    """A training block: a run of weeks under one intent."""

    __tablename__ = "blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    intent: Mapped[BlockIntent] = mapped_column(
        Enum(BlockIntent, name="block_intent"), nullable=False
    )
    planned_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[BlockStatus] = mapped_column(
        Enum(BlockStatus, name="block_status"),
        nullable=False,
        server_default=BlockStatus.active.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TrainingWeek(Base):
    """One prescribed week within a block."""

    __tablename__ = "training_weeks"

    id: Mapped[int] = mapped_column(primary_key=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("blocks.id"), nullable=False)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    week_index: Mapped[int] = mapped_column(Integer, nullable=False)
    days_planned: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[WeekStatus] = mapped_column(
        Enum(WeekStatus, name="week_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TrainingSession(Base):
    """One prescribed session (a training day) within a week."""

    __tablename__ = "training_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    week_id: Mapped[int] = mapped_column(
        ForeignKey("training_weeks.id"), nullable=False
    )
    athlete_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    session_type: Mapped[SessionType] = mapped_column(
        Enum(SessionType, name="session_type"), nullable=False
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="session_status"),
        nullable=False,
        server_default=SessionStatus.prescribed.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TrainingSet(Base):
    """One prescribed (and later executed) set within a session.

    Named `TrainingSet`, not `Set`, to avoid colliding with the builtin
    `set` / `typing.Set` (D9-6).
    """

    __tablename__ = "sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("training_sessions.id"), nullable=False
    )
    athlete_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), nullable=False)
    set_order: Mapped[int] = mapped_column(Integer, nullable=False)
    set_type: Mapped[SetType] = mapped_column(
        Enum(SetType, name="set_type"), nullable=False
    )
    intensity_type: Mapped[IntensityType] = mapped_column(
        Enum(IntensityType, name="intensity_type"), nullable=False
    )
    weight_mode: Mapped[WeightMode] = mapped_column(
        Enum(WeightMode, name="weight_mode"), nullable=False
    )
    equipment_config: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
    )
    prescribed_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    prescribed_reps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prescribed_intensity: Mapped[float | None] = mapped_column(Float, nullable=True)
    executed_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    executed_reps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    executed_intensity: Mapped[float | None] = mapped_column(Float, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
