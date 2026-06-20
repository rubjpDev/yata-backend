"""fold athlete_profiles into users (single-table athlete model)

Revision ID: 20260619_0005
Revises: 20260618_0003
Create Date: 2026-06-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260619_0005"
down_revision: str | Sequence[str] | None = "20260618_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# These enum types already exist in the database (created by 0002). Use the
# PostgreSQL-specific `postgresql.ENUM` (not the generic `sa.Enum`, which does
# not retain a `create_type` attribute and silently re-issues CREATE TYPE on
# `create_table`) with `create_type=False` so neither `add_column` nor
# `create_table` re-issues `CREATE TYPE` against them, and never call
# `.create()`/`.drop()` on them here — they stay alive throughout both
# `upgrade` and `downgrade`, only changing which table's columns own them.
discipline_enum = postgresql.ENUM("powerlifting", name="discipline", create_type=False)
unit_enum = postgresql.ENUM("kg", "lb", name="unit", create_type=False)
comp_style_enum = postgresql.ENUM(
    "raw", "classic", "equipped", name="comp_style", create_type=False
)

_DEFAULT_EQUIPMENT_OWNED = (
    '{"belt": false, "knee_sleeves": false, '
    '"knee_wraps": false, "wrist_wraps": false}'
)


def upgrade() -> None:
    """Add the 5 athlete columns to `users`, then drop `athlete_profiles`."""
    # `unit`/`comp_style`/`equipment_owned` ship with safe server defaults, so
    # a single NOT NULL add_column backfills existing rows atomically.
    op.add_column(
        "users",
        sa.Column(
            "unit",
            unit_enum,
            nullable=False,
            server_default="kg",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "comp_style",
            comp_style_enum,
            nullable=False,
            server_default="classic",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "equipment_owned",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=_DEFAULT_EQUIPMENT_OWNED,
        ),
    )
    op.add_column(
        "users",
        sa.Column("training_days_target", sa.Integer(), nullable=True),
    )

    # `discipline` has no natural business default. Non-destructive sequence:
    # add nullable -> backfill the only legal value today -> enforce NOT NULL.
    op.add_column(
        "users",
        sa.Column("discipline", discipline_enum, nullable=True),
    )
    op.execute("UPDATE users SET discipline = 'powerlifting' WHERE discipline IS NULL")
    op.alter_column("users", "discipline", nullable=False)

    # The shared enum types are now referenced by the new `users` columns, so
    # dropping `athlete_profiles` must NOT drop them.
    op.drop_table("athlete_profiles")


def downgrade() -> None:
    """Recreate `athlete_profiles` and drop the 5 athlete columns from users."""
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

    # Schema-only reversibility: this restores the table shape, not new-model
    # row content (no data migration mandate). Drop the 5 columns from users;
    # the enum types stay alive, now owned again by `athlete_profiles`.
    op.drop_column("users", "discipline")
    op.drop_column("users", "training_days_target")
    op.drop_column("users", "equipment_owned")
    op.drop_column("users", "comp_style")
    op.drop_column("users", "unit")
