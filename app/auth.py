"""Authentication routes: register, login, refresh, and /me."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import (
    AccessToken,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserRead,
)
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter()

_INVALID_CREDENTIALS_DETAIL = "Invalid credentials"
_CREDENTIALS_ERROR_DETAIL = "Could not validate credentials"


@router.post(
    "/auth/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest, session: AsyncSession = Depends(get_db)
) -> User:
    """Create a new athlete as a single user row."""
    existing = await session.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        display_name=payload.display_name,
        discipline=payload.discipline,
        comp_style=payload.comp_style,
        unit=payload.unit,
        equipment_owned=payload.equipment_owned.model_dump(),
    )
    session.add(user)
    await session.commit()
    return user


@router.post("/auth/login", response_model=TokenPair)
async def login(
    payload: LoginRequest, session: AsyncSession = Depends(get_db)
) -> TokenPair:
    """Authenticate a user and return an access/refresh token pair."""
    result = await session.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_CREDENTIALS_DETAIL,
        )

    return TokenPair(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/auth/refresh", response_model=AccessToken)
async def refresh(
    payload: RefreshRequest, session: AsyncSession = Depends(get_db)
) -> AccessToken:
    """Exchange a valid refresh token for a new access token."""
    token_payload = decode_token(payload.refresh_token, expected_type="refresh")
    user_id = int(token_payload["sub"])

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_CREDENTIALS_ERROR_DETAIL,
        )

    return AccessToken(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserRead)
async def read_current_user(user: User = Depends(get_current_user)) -> User:
    """Return the authenticated athlete's identity and athlete fields."""
    return user
