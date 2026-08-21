"""Tests for the R39-R41 embedding warm-up: both branches, nothing downloaded."""

import asyncio
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from app.config import settings
from app.main import _warm_embedding_client, lifespan


@pytest.fixture
def production_environment() -> Iterator[None]:
    """Force `settings.environment == "production"` for one test."""
    original = settings.environment
    settings.environment = "production"
    yield
    settings.environment = original


async def test_warm_embedding_client_calls_get_embedding_client() -> None:
    """The warm-up calls the cached, injectable client factory, not a new instance."""
    with patch("app.main.get_embedding_client") as fake_get_client:
        await _warm_embedding_client()
    fake_get_client.assert_called_once_with()


async def test_warm_embedding_client_swallows_exceptions() -> None:
    """A raised warm-up is logged and swallowed; it must not propagate (R41)."""
    with patch("app.main.get_embedding_client", side_effect=RuntimeError("boom")):
        await _warm_embedding_client()  # must not raise


async def test_lifespan_schedules_warmup_in_production(
    production_environment: None,
) -> None:
    """WHERE environment == "production" a background warm-up task is scheduled."""
    with patch("app.main.asyncio.create_task") as fake_create_task:
        async with lifespan(None):  # type: ignore[arg-type]
            pass
    fake_create_task.assert_called_once()
    fake_create_task.call_args.args[0].close()  # never scheduled: avoid the warning


async def test_lifespan_skips_warmup_outside_production() -> None:
    """Local runs and the offline test suite never schedule a warm-up (R41)."""
    assert settings.environment != "production"
    with patch("app.main.asyncio.create_task") as fake_create_task:
        async with lifespan(None):  # type: ignore[arg-type]
            pass
    fake_create_task.assert_not_called()


async def test_lifespan_never_awaits_the_warmup_task() -> None:
    """`lifespan` yields immediately: it schedules, never awaits, the warm-up."""
    settings.environment = "production"
    try:
        started = asyncio.Event()
        blocked = asyncio.Event()

        async def _slow_warmup() -> None:
            started.set()
            await blocked.wait()

        with patch("app.main._warm_embedding_client", side_effect=_slow_warmup):
            async with lifespan(None):  # type: ignore[arg-type]
                await asyncio.wait_for(started.wait(), timeout=1)
        blocked.set()
    finally:
        settings.environment = "local"
