"""User-facing response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.user import CompStyle, Discipline, Unit


class AthleteProfileRead(BaseModel):
    """Public representation of an athlete profile."""

    model_config = ConfigDict(from_attributes=True)

    discipline: Discipline
    unit: Unit
    comp_style: CompStyle
    equipment_owned: dict[str, bool]
    training_days_target: int | None


class UserRead(BaseModel):
    """Public representation of a user; never includes password material."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    created_at: datetime
    updated_at: datetime
    profile: AthleteProfileRead
