"""Application configuration loaded from environment variables."""

from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode


def _split_csv(value: object) -> object:
    """Accept a comma-separated string for list settings read from the shell.

    `pydantic-settings` parses list-typed fields from the environment as
    JSON by default, which is a surprising trap for a plain
    `CORS_ORIGINS=a,b` env var — it raises before this validator even runs.
    `NoDecode` opts the field out of that JSON pre-parsing so the raw string
    reaches this function, which then splits it by comma; a value that is
    already a list (e.g. the Python default) passes through unchanged.
    """
    if isinstance(value, str):
        return [origin.strip() for origin in value.split(",") if origin.strip()]
    return value


CommaSeparatedList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]


class Settings(BaseSettings):
    """Runtime settings read from environment variables."""

    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/yata"
    redis_url: str = "redis://localhost:6379"
    secret_key: str = "change-me-in-production"
    environment: str = "local"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7

    # Amazon Bedrock, eu-west-1, Qwen3 Next 80B A3B (R0-A). `llm_model` and
    # `llm_region` drive the live implementation, `BedrockConverseClient`
    # (R15 path B — see progress/impl_yata-0013-coach-graph-and-human-gate.md
    # for why path A's bearer credential was not reachable in pre-flight).
    # `llm_base_url`/`llm_api_key` are kept, unused today, as the R17-required
    # settings for path A: the day a Bedrock API key exists, only `app/llm.py`
    # changes, not this file. Every default here makes the default test suite
    # work with no environment variable set (R17): the fake LLMClient is what
    # the offline suite injects.
    llm_base_url: str = "https://bedrock-runtime.eu-west-1.amazonaws.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen.qwen3-next-80b-a3b"
    llm_region: str = "eu-west-1"
    llm_timeout_seconds: float = 60.0

    # Dedicated opt-in gate for the real Bedrock client (yata-0018): ambient
    # AWS credentials must never be enough on their own to reach a paid API,
    # so this is a switch of its own, independent of `environment`. Anything
    # other than "bedrock" — including the default — makes
    # `app.deps.get_llm_client` serve `app.llm.FakeLLMClient` instead. Set to
    # "bedrock" in `docker-compose.prod.yml`'s `api` service.
    llm_provider: str = Field(default="fake", validation_alias="YATA_LLM")

    # Origins allowed to call the API cross-origin (CORS, yata-0026). Never
    # "*": the API is publicly deployed. Comma-separated in the environment
    # (`CommaSeparatedList`), defaulting to the Vite dev server; add the
    # deployed frontend's origin here once it exists.
    cors_origins: CommaSeparatedList = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
