"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
