"""Exercise catalog ORM model"""

from enum import StrEnum
from sqlalchemy import JSON, String, Enum, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import ARRAY
from app.db.base import Base
from datetime import datetime

class ExerciseCategory(StrEnum):
    squat = "squat"
    bench = "bench"
    deadlift = "deadlift"
    accessory = "accessory"

class Exercise(Base):
    """A catalog exercise: either system-wide (created_by NULL) or a user custom"""

    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[ExerciseCategory] = mapped_column(
        Enum(ExerciseCategory, name ="exercise_category"),nullable=False
    )
    muscle_groups: Mapped[list[str]] = mapped_column(
        JSON().with_variant(ARRAY(String), "postgresql"),
        nullable=False,
        default=list
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    ) 