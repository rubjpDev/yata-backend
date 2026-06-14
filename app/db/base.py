"""Declarative base shared by all SQLAlchemy ORM models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models; its metadata drives Alembic and tests."""
