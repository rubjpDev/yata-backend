"""All Pydantic v2 request and response schemas."""

from datetime import date as Date
from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
)

from app.models import (
    BlockIntent,
    BlockStatus,
    CompStyle,
    Discipline,
    ExerciseCategory,
    Unit,
    WeekStatus,
)

PasswordStr = Annotated[
    str,
    StringConstraints(min_length=8, max_length=16, pattern=r"^[!-~]+$"),
]


class EquipmentOwned(BaseModel):
    """Competition equipment an athlete owns, all defaulting to unowned."""

    belt: bool = False
    knee_sleeves: bool = False
    knee_wraps: bool = False
    wrist_wraps: bool = False


class RegisterRequest(BaseModel):
    """Payload for POST /v1/auth/register."""

    email: EmailStr
    password: PasswordStr
    display_name: str
    discipline: Discipline
    comp_style: CompStyle = CompStyle.classic
    unit: Unit = Unit.kg
    equipment_owned: EquipmentOwned = EquipmentOwned()


class LoginRequest(BaseModel):
    """Payload for POST /v1/auth/login."""

    email: EmailStr
    password: str


class TokenPair(BaseModel):
    """Response for a successful login: access + refresh tokens."""

    access_token: str
    refresh_token: str


class AccessToken(BaseModel):
    """Response for a successful token refresh: a new access token."""

    access_token: str


class RefreshRequest(BaseModel):
    """Payload for POST /v1/auth/refresh."""

    refresh_token: str


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


class ExerciseCreate(BaseModel):
    """Payload for POST /v1/exercises (user-created exercises only)."""

    name: str = Field(min_length=1, max_length=255)
    category: ExerciseCategory
    muscle_groups: list[str] = Field(default_factory=list)


class ExerciseRead(BaseModel):
    """Public representation of an exercise; `is_custom` derived from created_by."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: ExerciseCategory
    muscle_groups: list[str]
    created_by: int | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_custom(self) -> bool:
        """True when the exercise belongs to a user (created_by is not NULL)."""
        return self.created_by is not None


class BodyweightCreate(BaseModel):
    """Payload for POST /v1/bodyweight (upsert by athlete + bodyweight)"""

    date: Date
    weight_kg: float = Field(gt=0.0)

    @field_validator("date")
    @classmethod
    def date_not_in_future(cls, value: Date) -> Date:
        """Reject logs dates after today (server-local date)"""

        if value > Date.today():
            raise ValueError("Date cannot be in the future")
        return value


class BodyweightRead(BaseModel):
    """Public representation of bodyweight log"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    athlete_id: int
    date: Date
    weight_kg: float
    created_at: datetime
    updated_at: datetime


class BlockCreate(BaseModel):
    """Payload for POST /v1/blocks: creates the block and its first week."""

    intent: BlockIntent
    planned_weeks: int = Field(ge=1)
    start_date: Date
    days: int = Field(ge=1, le=4)
    seed_1rm_kg: dict[str, float] = Field(default_factory=dict)


class WeekRead(BaseModel):
    """Public representation of a training week."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    block_id: int
    week_index: int
    days_planned: int
    status: WeekStatus
    created_at: datetime


class BlockRead(BaseModel):
    """Public representation of a block, with its training weeks."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    athlete_id: int
    intent: BlockIntent
    planned_weeks: int
    start_date: Date
    status: BlockStatus
    created_at: datetime
    weeks: list[WeekRead] = Field(default_factory=list)
