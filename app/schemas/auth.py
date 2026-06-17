"""Authentication request and response schemas."""

from typing import Annotated

from pydantic import BaseModel, EmailStr, StringConstraints

from app.models.user import CompStyle, Discipline, Unit

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
