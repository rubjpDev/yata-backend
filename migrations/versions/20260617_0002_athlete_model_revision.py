"""drop role/profiles, add is_admin and athlete_profiles

Revision ID: 20260617_0002
Revises: 20260614_0001
Create Date: 2026-06-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260617_0002"
down_revision: str | Sequence[str] | None = "20260614_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

user_role_enum = sa.Enum("trainer", "trainee", name="user_role")
discipline_enum = sa.Enum("powerlifting", name="discipline")
unit_enum = sa.Enum("kg", "lb", name="unit")
comp_style_enum = sa.Enum("raw", "classic", "equipped", name="comp_style")

_DEFAULT_EQUIPMENT_OWNED = (
    '{"belt": false, "knee_sleeves": false, '
    '"knee_wraps": false, "wrist_wraps": false}'
)


def upgrade() -> None:
    """Drop the trainer/trainee model; add is_admin and athlete_profiles."""
    op.drop_table("trainee_profiles")
    op.drop_table("trainer_profiles")

    op.drop_column("users", "role")
    user_role_enum.drop(op.get_bind(), checkfirst=True)

    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "athlete_profiles",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("discipline", discipline_enum, nullable=False),
        sa.Column(
            "unit",
            unit_enum,
            nullable=False,
            server_default="kg",
        ),
        sa.Column(
            "comp_style",
            comp_style_enum,
            nullable=False,
            server_default="classic",
        ),
        sa.Column(
            "equipment_owned",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=_DEFAULT_EQUIPMENT_OWNED,
        ),
        sa.Column("training_days_target", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )


def downgrade() -> None:
    """Restore the prior D3 trainer/trainee schema shape."""
    op.drop_table("athlete_profiles")
    # `drop_table` does not reliably drop the enum types of the columns it
    # held, so drop them explicitly (mirrors the `user_role_enum.drop` below).
    comp_style_enum.drop(op.get_bind(), checkfirst=True)
    unit_enum.drop(op.get_bind(), checkfirst=True)
    discipline_enum.drop(op.get_bind(), checkfirst=True)

    op.drop_column("users", "is_admin")

    # `add_column` (unlike `create_table`) does not auto-issue CREATE TYPE for
    # an enum column, so the type must be created explicitly first.
    user_role_enum.create(op.get_bind(), checkfirst=True)
    # Schema-only downgrade: add nullable + backfill so it also succeeds
    # against a table that already holds rows, then enforce NOT NULL.
    op.add_column(
        "users",
        sa.Column("role", user_role_enum, nullable=True),
    )
    op.execute("UPDATE users SET role = 'trainee' WHERE role IS NULL")
    op.alter_column("users", "role", nullable=False)

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
