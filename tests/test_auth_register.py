"""Tests for POST /v1/auth/register."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import TraineeProfile, TrainerProfile, User


async def test_register_returns_201_with_user_and_no_password(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """A valid registration returns 201 with the serialized user, no hash."""
    response = await client.post("/v1/auth/register", json=register_payload)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == register_payload["email"]
    assert body["role"] == "trainee"
    assert body["display_name"] == register_payload["display_name"]
    assert "hashed_password" not in body
    assert "password" not in body


async def test_register_creates_matching_trainee_profile(
    client: AsyncClient, db_session: AsyncSession, register_payload: dict[str, str]
) -> None:
    """Registering a trainee creates the matching trainee_profiles row."""
    response = await client.post("/v1/auth/register", json=register_payload)
    user_id = response.json()["id"]

    result = await db_session.execute(
        select(TraineeProfile).where(TraineeProfile.user_id == user_id)
    )
    assert result.scalar_one_or_none() is not None


async def test_register_creates_matching_trainer_profile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Registering a trainer creates the matching trainer_profiles row."""
    payload = {
        "email": "trainer@example.com",
        "password": "Sup3rSecret!",
        "role": "trainer",
        "display_name": "Test Trainer",
    }
    response = await client.post("/v1/auth/register", json=payload)
    user_id = response.json()["id"]

    result = await db_session.execute(
        select(TrainerProfile).where(TrainerProfile.user_id == user_id)
    )
    assert result.scalar_one_or_none() is not None


async def test_register_duplicate_email_returns_409(
    client: AsyncClient, db_session: AsyncSession, register_payload: dict[str, str]
) -> None:
    """A second registration with the same email returns 409 and no new user."""
    first = await client.post("/v1/auth/register", json=register_payload)
    assert first.status_code == 201

    second = await client.post("/v1/auth/register", json=register_payload)
    assert second.status_code == 409

    result = await db_session.execute(
        select(User).where(User.email == register_payload["email"])
    )
    assert len(result.scalars().all()) == 1


async def test_register_password_too_short_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A password shorter than 8 chars is rejected with 422 and no user."""
    payload = {
        "email": "short@example.com",
        "password": "Sh0rt!",
        "role": "trainee",
        "display_name": "Short Pass",
    }
    response = await client.post("/v1/auth/register", json=payload)

    assert response.status_code == 422
    result = await db_session.execute(
        select(User).where(User.email == payload["email"])
    )
    assert result.scalar_one_or_none() is None


async def test_register_password_too_long_returns_422(client: AsyncClient) -> None:
    """A password longer than 16 chars is rejected with 422."""
    payload = {
        "email": "long@example.com",
        "password": "A" * 17 + "1!",
        "role": "trainee",
        "display_name": "Long Pass",
    }
    response = await client.post("/v1/auth/register", json=payload)

    assert response.status_code == 422


async def test_register_password_with_space_returns_422(client: AsyncClient) -> None:
    """A password containing a space character is rejected with 422."""
    payload = {
        "email": "space@example.com",
        "password": "Bad Pass1",
        "role": "trainee",
        "display_name": "Space Pass",
    }
    response = await client.post("/v1/auth/register", json=payload)

    assert response.status_code == 422


async def test_register_password_with_control_char_returns_422(
    client: AsyncClient,
) -> None:
    """A password containing a control character is rejected with 422."""
    payload = {
        "email": "control@example.com",
        "password": "Bad\tPass1",
        "role": "trainee",
        "display_name": "Control Pass",
    }
    response = await client.post("/v1/auth/register", json=payload)

    assert response.status_code == 422
