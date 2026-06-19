"""Authentication router: register, login, refresh, and /me."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import EmailAlreadyExists, InvalidCredentials, InvalidToken
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.auth import (
    AccessToken,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)
from app.schemas.user import UserRead
from app.services import auth_service

router = APIRouter()

_INVALID_CREDENTIALS_DETAIL = "Invalid credentials"
_INVALID_TOKEN_DETAIL = "Could not validate credentials"
_USER_NOT_FOUND_DETAIL = "Could not validate credentials"


@router.post(
    "/auth/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest, session: AsyncSession = Depends(get_db)
) -> User:
    """Create a new athlete as a single user row."""
    try:
        return await auth_service.register(
            session,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            discipline=payload.discipline,
            comp_style=payload.comp_style,
            unit=payload.unit,
            equipment_owned=payload.equipment_owned.model_dump(),
        )
    except EmailAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.post("/auth/login", response_model=TokenPair)
async def login(
    payload: LoginRequest, session: AsyncSession = Depends(get_db)
) -> TokenPair:
    """Authenticate a user and return an access/refresh token pair."""
    try:
        return await auth_service.authenticate(
            session, email=payload.email, password=payload.password
        )
    except InvalidCredentials as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_CREDENTIALS_DETAIL,
        ) from exc


@router.post("/auth/refresh", response_model=AccessToken)
async def refresh(
    payload: RefreshRequest, session: AsyncSession = Depends(get_db)
) -> AccessToken:
    """Exchange a valid refresh token for a new access token."""
    try:
        access_token = await auth_service.refresh_access(
            session, refresh_token=payload.refresh_token
        )
    except (InvalidToken, InvalidCredentials) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_TOKEN_DETAIL,
        ) from exc

    return AccessToken(access_token=access_token)


@router.get("/me", response_model=UserRead)
async def read_current_user(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Return the authenticated athlete's identity and athlete fields."""
    current = await auth_service.get_user_by_id(session, user.id)
    if current is None:  # pragma: no cover - defensive: user just resolved above
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_USER_NOT_FOUND_DETAIL,
        )
    return current
