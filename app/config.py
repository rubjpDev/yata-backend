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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
