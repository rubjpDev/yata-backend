"""create users, trainer_profiles, trainee_profiles

Revision ID: 20260614_0001
Revises:
Create Date: 2026-06-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260614_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

user_role_enum = sa.Enum("trainer", "trainee", name="user_role")


def upgrade() -> None:
    """Create users, trainer_profiles, and trainee_profiles tables."""
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            user_role_enum,
            nullable=False,
        ),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_unique_constraint("uq_users_email", "users", ["email"])

    op.create_table(
        "trainer_profiles",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )

    op.create_table(
        "trainee_profiles",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )


def downgrade() -> None:
    """Drop trainee_profiles, trainer_profiles, users, and the user_role enum."""
    op.drop_table("trainee_profiles")
    op.drop_table("trainer_profiles")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_table("users")

    user_role_enum.drop(op.get_bind(), checkfirst=True)
