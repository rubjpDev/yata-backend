"""Tests for CORS on /v1/auth/login (yata-0026)."""

from httpx import AsyncClient

ALLOWED_ORIGIN = "http://localhost:5173"
DISALLOWED_ORIGIN = "http://evil.example.com"


async def test_preflight_from_allowed_origin_is_echoed_back(
    client: AsyncClient,
) -> None:
    """An OPTIONS preflight from an allowed origin gets that origin echoed back."""
    response = await client.options(
        "/v1/auth/login",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN


async def test_preflight_from_disallowed_origin_gets_no_cors_header(
    client: AsyncClient,
) -> None:
    """An OPTIONS preflight from an origin outside the allow-list gets no header."""
    response = await client.options(
        "/v1/auth/login",
        headers={
            "Origin": DISALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )

    assert "access-control-allow-origin" not in response.headers
