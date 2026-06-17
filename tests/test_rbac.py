"""Tests for the reusable require_role RBAC dependency."""

from collections.abc import AsyncGenerator

import pytest_asyncio
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_role
from app.main import app
from app.models.user import Role, User

_probe_router = APIRouter()


@_probe_router.get("/v1/trainer-only")
async def trainer_only_probe(
    user: User = Depends(require_role(Role.trainer)),
) -> dict[str, str]:
    """A probe route reachable only by trainers."""
    return {"email": user.email}


app.include_router(_probe_router)


@pytest_asyncio.fixture
async def rbac_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """An AsyncClient sharing the test DB, for RBAC probe requests."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.pop(get_db, None)


async def _register_and_login(client: AsyncClient, role: str, email: str) -> str:
    """Register a user with `role` and return their access token."""
    await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "Sup3rSecret!",
            "role": role,
            "display_name": "Probe User",
        },
    )
    login = await client.post(
        "/v1/auth/login", json={"email": email, "password": "Sup3rSecret!"}
    )
    return str(login.json()["access_token"])


async def test_trainer_only_route_allows_trainer(rbac_client: AsyncClient) -> None:
    """A trainer with a valid token gets 200 from the trainer-only route."""
    token = await _register_and_login(
        rbac_client, "trainer", "trainer-rbac@example.com"
    )

    response = await rbac_client.get(
        "/v1/trainer-only", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200


async def test_trainer_only_route_forbids_trainee(rbac_client: AsyncClient) -> None:
    """A trainee with a valid token gets 403 from the trainer-only route."""
    token = await _register_and_login(
        rbac_client, "trainee", "trainee-rbac@example.com"
    )

    response = await rbac_client.get(
        "/v1/trainer-only", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403


async def test_trainer_only_route_requires_authentication(
    rbac_client: AsyncClient,
) -> None:
    """No token gets 401 from the trainer-only route."""
    response = await rbac_client.get("/v1/trainer-only")

    assert response.status_code == 401
