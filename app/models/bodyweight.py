"""Bodyweight logging ORM model"""

from sqlalchemy import Float,ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from sqlalchemy.types import Date
from datetime import date, datetime
class BodyweightLog(Base):
    """A single bodyweight log entry for an athlete"""

    __tablename__ = "bodyweight_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)