"""Shared pytest fixtures: async SQLite test DB and an httpx test client."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Import the models package so Base.metadata is fully populated before
# create_all (users, exercises, bodyweight_logs all register here).
import app.models as _models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """Provide an isolated in-memory SQLite database per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """Provide an httpx AsyncClient with `get_db` overridden to the test DB."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def register_payload() -> dict[str, str]:
    """A valid registration payload reusable across tests."""
    return {
        "email": "athlete@example.com",
        "password": "Sup3rSecret!",
        "display_name": "Test Athlete",
        "discipline": "powerlifting",
    }
