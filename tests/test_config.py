"""Unit tests for app.config: the comma-separated CORS_ORIGINS env parsing.

`pydantic-settings` parses list-typed fields from the environment as JSON by
default, so a plain `CORS_ORIGINS=a,b` would otherwise fail to load. This is
not obvious from reading the field alone, hence a dedicated test.
"""

import pytest

from app.config import Settings


def test_cors_origins_default_is_the_vite_dev_server() -> None:
    """With no env var set, cors_origins defaults to the Vite dev origins."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origins == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_cors_origins_parses_comma_separated_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A comma-separated CORS_ORIGINS env var is split into a list, not JSON."""
    monkeypatch.setenv(
        "CORS_ORIGINS", "https://app.example.com,https://staging.example.com"
    )

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origins == [
        "https://app.example.com",
        "https://staging.example.com",
    ]
