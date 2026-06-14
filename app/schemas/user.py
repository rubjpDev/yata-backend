"""User-facing response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.user import Role


class UserRead(BaseModel):
    """Public representation of a user; never includes password material."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: Role
    display_name: str
    created_at: datetime
    updated_at: datetime
