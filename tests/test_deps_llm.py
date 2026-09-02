"""`get_llm_client()`'s explicit opt-in gate (yata-0018).

Every test clears `get_llm_client.cache_clear()` before and after, because
the function is `@lru_cache`d (R18) — a return value cached by an earlier
test would silently poison every test that runs after it.
"""

import asyncio
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from app import deps
from app.config import settings
from app.deps import get_llm_client
from app.llm import BedrockConverseClient, FakeLLMClient
from app.main import lifespan


@pytest.fixture(autouse=True)
def _clear_llm_client_cache() -> Iterator[None]:
    """Isolate each test from the `@lru_cache`d client and `settings.llm_provider`."""
    original_provider = settings.llm_provider
    get_llm_client.cache_clear()
    yield
    settings.llm_provider = original_provider
    get_llm_client.cache_clear()


def test_default_llm_provider_returns_the_fake() -> None:
    """No `YATA_LLM` set (the default) serves the deterministic fake, not Bedrock."""
    settings.llm_provider = "fake"
    assert isinstance(get_llm_client(), FakeLLMClient)


def test_ambient_aws_credentials_alone_do_not_select_bedrock() -> None:
    """Ambient AWS credentials must never be sufficient (the bug this fixes)."""
    settings.llm_provider = "fake"
    with patch("app.deps.boto3.Session") as fake_session_cls:
        fake_session_cls.return_value.get_credentials.return_value = MagicMock()
        client = get_llm_client()
    assert isinstance(client, FakeLLMClient)
    fake_session_cls.assert_not_called()


def test_explicit_optin_with_credentials_returns_bedrock_without_calling_it() -> None:
    """`YATA_LLM=bedrock` plus resolvable credentials returns the real client.

    `BedrockConverseClient.__init__` only reads settings and never touches
    the network (its boto3 client is built lazily in `_get_client`), so
    asserting the return type here never risks a real, billed call.
    """
    settings.llm_provider = "bedrock"
    with patch("app.deps.boto3.Session") as fake_session_cls:
        fake_session_cls.return_value.get_credentials.return_value = MagicMock()
        client = get_llm_client()
    assert isinstance(client, BedrockConverseClient)


def test_optin_without_credentials_fails_loudly_with_actionable_message() -> None:
    """`YATA_LLM=bedrock` with no credentials raises, never returns a client."""
    settings.llm_provider = "bedrock"
    with patch("app.deps.boto3.Session") as fake_session_cls:
        fake_session_cls.return_value.get_credentials.return_value = None
        with pytest.raises(RuntimeError, match="YATA_LLM=bedrock"):
            get_llm_client()


def test_lifespan_builds_the_client_eagerly_when_opted_in() -> None:
    """`app.main.lifespan` calls `get_llm_client()` itself when opted in.

    So a missing credential fails at startup, never silently on the first
    coach request.
    """

    async def _enter_lifespan() -> None:
        async with lifespan(None):  # type: ignore[arg-type]
            pass

    settings.llm_provider = "bedrock"
    with patch.object(deps, "boto3") as fake_boto3:
        fake_boto3.Session.return_value.get_credentials.return_value = None
        with pytest.raises(RuntimeError, match="YATA_LLM=bedrock"):
            asyncio.run(_enter_lifespan())
