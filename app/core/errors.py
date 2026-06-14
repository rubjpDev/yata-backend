"""Domain-level exceptions raised by services and security helpers.

These are translated to `HTTPException` only at the router edge.
"""


class EmailAlreadyExists(Exception):
    """Raised when registering with an email that is already taken."""


class InvalidCredentials(Exception):
    """Raised when login credentials do not match an existing user."""


class InvalidToken(Exception):
    """Raised when a JWT is missing, malformed, expired, or of the wrong type."""
