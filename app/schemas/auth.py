"""Authentication request and response schemas."""

from typing import Annotated

from pydantic import BaseModel, EmailStr, StringConstraints

from app.models.user import Role

PasswordStr = Annotated[
    str,
    StringConstraints(min_length=8, max_length=16, pattern=r"^[!-~]+$"),
]


class RegisterRequest(BaseModel):
    """Payload for POST /v1/auth/register."""

    email: EmailStr
    password: PasswordStr
    role: Role
    display_name: str


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
