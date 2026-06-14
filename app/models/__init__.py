"""ORM models package; imports populate `Base.metadata` for Alembic and tests."""

from app.models.user import Role, TraineeProfile, TrainerProfile, User

__all__ = ["Role", "TraineeProfile", "TrainerProfile", "User"]
