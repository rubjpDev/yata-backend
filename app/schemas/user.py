"""User-facing response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.user import CompStyle, Discipline, Unit


class UserRead(BaseModel):
    """Public representation of a user; never includes password material.

    Flat single-table athlete shape: identity and athlete fields live on the
    same `users` row, so there is no nested `profile` sub-resource.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    discipline: Discipline
    unit: Unit
    comp_style: CompStyle
    equipment_owned: dict[str, bool]
    training_days_target: int | None
    created_at: datetime
    updated_at: datetime
