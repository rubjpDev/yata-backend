"""Tests for POST /v1/auth/register."""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def test_register_returns_201_with_user_and_no_password(
    client: AsyncClient, register_payload: dict[str, str]
) -> None:
    """A valid registration returns 201 with the serialized user, no hash."""
    response = await client.post("/v1/auth/register", json=register_payload)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == register_payload["email"]
    assert body["display_name"] == register_payload["display_name"]
    assert "hashed_password" not in body
    assert "password" not in body
    assert "role" not in body


async def test_register_with_explicit_profile_fields_persists_them(
    client: AsyncClient,
) -> None:
    """Explicit discipline/comp_style/unit/equipment_owned are persisted as given."""
    payload = {
        "email": "explicit@example.com",
        "password": "Sup3rSecret!",
        "display_name": "Explicit Athlete",
        "discipline": "powerlifting",
        "comp_style": "raw",
        "unit": "lb",
        "equipment_owned": {
            "belt": True,
            "knee_sleeves": True,
            "knee_wraps": False,
            "wrist_wraps": True,
        },
    }
    response = await client.post("/v1/auth/register", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["discipline"] == "powerlifting"
    assert body["comp_style"] == "raw"
    assert body["unit"] == "lb"
    assert body["equipment_owned"] == {
        "belt": True,
        "knee_sleeves": True,
        "knee_wraps": False,
        "wrist_wraps": True,
    }


async def test_register_creates_single_user_row(
    client: AsyncClient, db_session: AsyncSession, register_payload: dict[str, str]
) -> None:
    """Registering an athlete creates exactly one users row with its fields."""
    response = await client.post("/v1/auth/register", json=register_payload)
    user_id = response.json()["id"]

    result = await db_session.execute(select(User).where(User.id == user_id))
    created = result.scalar_one_or_none()
    assert created is not None
    assert created.discipline == "powerlifting"


async def test_register_omitted_optionals_apply_defaults(
    client: AsyncClient,
) -> None:
    """Omitted comp_style/unit/equipment_owned get classic/kg/all-false defaults."""
    payload = {
        "email": "defaults@example.com",
        "password": "Sup3rSecret!",
        "display_name": "Defaults Athlete",
        "discipline": "powerlifting",
    }
    response = await client.post("/v1/auth/register", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["comp_style"] == "classic"
    assert body["unit"] == "kg"
    assert body["equipment_owned"] == {
        "belt": False,
        "knee_sleeves": False,
        "knee_wraps": False,
        "wrist_wraps": False,
    }


async def test_register_missing_discipline_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A registration without `discipline` is rejected with 422 and no rows."""
    payload = {
        "email": "missing-discipline@example.com",
        "password": "Sup3rSecret!",
        "display_name": "No Discipline",
    }
    response = await client.post("/v1/auth/register", json=payload)

    assert response.status_code == 422
    result = await db_session.execute(
        select(User).where(User.email == payload["email"])
    )
    assert result.scalar_one_or_none() is None


async def test_register_invalid_discipline_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An unsupported discipline value is rejected with 422 and no rows."""
    payload = {
        "email": "bad-discipline@example.com",
        "password": "Sup3rSecret!",
        "display_name": "Bad Discipline",
        "discipline": "bodybuilding",
    }
    response = await client.post("/v1/auth/register", json=payload)

    assert response.status_code == 422
    result = await db_session.execute(
        select(User).where(User.email == payload["email"])
    )
    assert result.scalar_one_or_none() is None


async def test_register_invalid_comp_style_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An unsupported comp_style value is rejected with 422 and no rows."""
    payload = {
        "email": "bad-comp-style@example.com",
        "password": "Sup3rSecret!",
        "display_name": "Bad Comp Style",
        "discipline": "powerlifting",
        "comp_style": "unlimited",
    }
    response = await client.post("/v1/auth/register", json=payload)

    assert response.status_code == 422
    result = await db_session.execute(
        select(User).where(User.email == payload["email"])
    )
    assert result.scalar_one_or_none() is None


async def test_register_invalid_unit_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An unsupported unit value is rejected with 422 and no rows."""
    payload = {
        "email": "bad-unit@example.com",
        "password": "Sup3rSecret!",
        "display_name": "Bad Unit",
        "discipline": "powerlifting",
        "unit": "stone",
    }
    response = await client.post("/v1/auth/register", json=payload)

    assert response.status_code == 422
    result = await db_session.execute(
        select(User).where(User.email == payload["email"])
    )
    assert result.scalar_one_or_none() is None


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
        "display_name": "Short Pass",
        "discipline": "powerlifting",
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
        "display_name": "Long Pass",
        "discipline": "powerlifting",
    }
    response = await client.post("/v1/auth/register", json=payload)

    assert response.status_code == 422


async def test_register_password_with_space_returns_422(client: AsyncClient) -> None:
    """A password containing a space character is rejected with 422."""
    payload = {
        "email": "space@example.com",
        "password": "Bad Pass1",
        "display_name": "Space Pass",
        "discipline": "powerlifting",
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
        "display_name": "Control Pass",
        "discipline": "powerlifting",
    }
    response = await client.post("/v1/auth/register", json=payload)

    assert response.status_code == 422
