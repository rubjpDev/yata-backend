"""LLM clients: the injectable interface and its Bedrock implementation.

Mirrors `app.embeddings.EmbeddingClient`: a `Protocol`, one real implementation,
one deterministic fake living in the tests (R19). `async` because the call is
network I/O inside an async route — a blocking call stalls the event loop for
the whole proposal (`EmbeddingClient` is sync because it is local CPU work,
this is not).

R15/R62 pre-flight (Amazon Bedrock, eu-west-1, Qwen3 Next 80B A3B, R0-A):
**path B** — `boto3` + `bedrock-runtime` Converse, wrapped in
`asyncio.to_thread` because `boto3` is synchronous — is the implementation.
Path A (a plain `httpx` `POST {base_url}/chat/completions`) was tried first,
per R15's stated preference, and the endpoint genuinely exists and serves this
model in `eu-west-1` — but only under a **Bedrock API key** (bearer auth),
which is a credential generated through the Bedrock console UI, with no CLI
or SDK equivalent found in this account. Without that credential path A's
whole reason for existing — zero new dependencies, no request signing — is
unavailable, so path B is what is implemented. Both live-call findings are
recorded verbatim in `progress/impl_yata-0013-coach-graph-and-human-gate.md`.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Protocol

import boto3

from app.config import settings


@dataclass(frozen=True)
class LLMResponse:
    """One completion, with the observability fields `agent_runs` records.

    A client that cannot report tokens returns `None` for them rather than a
    guess — the same "refuse rather than extrapolate" voice as `engine.load_for`.
    """

    text: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int


class LLMClient(Protocol):
    """Turns a system+user prompt into a completion. One operation, on purpose."""

    async def complete(self, *, system: str, user: str) -> LLMResponse:
        """Run one completion; no node, route or helper constructs a client (R16)."""
        ...


class LLMRequestError(RuntimeError):
    """The LLM call failed or timed out (R50: the route answers 503 for this)."""


class BedrockConverseClient:
    """`LLMClient` over `bedrock-runtime`'s Converse API (R15 path B).

    `boto3` is synchronous, so every call runs in a worker thread via
    `asyncio.to_thread` — otherwise a single completion would stall the whole
    event loop for as long as the model takes to answer (observed up to ~170s
    on a cold model in pre-flight; see the impl report).
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        region: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._model = model or settings.llm_model
        self._region = region or settings.llm_region
        self._timeout_seconds = timeout_seconds or settings.llm_timeout_seconds
        self._client: Any = None

    def _get_client(self) -> Any:
        """Build the boto3 client on first use, never at import."""
        if self._client is None:
            from botocore.config import Config

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self._region,
                config=Config(read_timeout=self._timeout_seconds, connect_timeout=30),
            )
        return self._client

    def _converse_sync(self, *, system: str, user: str) -> dict[str, Any]:
        client = self._get_client()
        return dict(
            client.converse(
                modelId=self._model,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
            )
        )

    async def complete(self, *, system: str, user: str) -> LLMResponse:
        """Run one Converse call in a thread; raises `LLMRequestError` on failure."""
        started = time.monotonic()
        try:
            body = await asyncio.to_thread(
                self._converse_sync, system=system, user=user
            )
        except Exception as exc:  # noqa: BLE001 - boto3 raises its own hierarchy
            raise LLMRequestError(f"LLM request failed: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        usage = body.get("usage") or {}
        content = body["output"]["message"]["content"]
        text = "".join(block.get("text", "") for block in content)
        return LLMResponse(
            text=text,
            model=self._model,
            prompt_tokens=usage.get("inputTokens"),
            completion_tokens=usage.get("outputTokens"),
            latency_ms=latency_ms,
        )
