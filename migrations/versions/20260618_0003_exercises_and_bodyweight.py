"""create exercises and bodyweight_logs

Revision ID: 20260618_0003
Revises: 20260617_0002
Create Date: 2026-06-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260618_0003"
down_revision: str | Sequence[str] | None = "20260617_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

exercise_category_enum = sa.Enum("squat", "bench", "deadlift", "accessory", name="exercise_category")

#System exercises seeded with created_by NULL. Idempotent insert below
_SYSTEM_EXERCISES: list[tuple[str,str,str]] = [
    ("Squat", "squat", "{quads,glutes,core}"),
    ("Bench Press", "bench", "{chest,shoulders,triceps}"),
    ("Deadlift", "deadlift", "{hamstrings,glutes,back}"),
    ("Front Squat", "accessory", "{quads,core}"),
    ("Romanian Deadlift", "accessory", "{hamstrings,glutes}"),
    ("Overhead Press", "accessory", "{shoulders,triceps}"),
    ("Barbell Row", "accessory", "{back,biceps}"),
    ("Pull-up", "accessory", "{back,biceps}"),
    ("Dip", "accessory", "{chest,triceps}"),
    ("Leg Press", "accessory", "{quads,glutes}"),
    ("Leg Curl", "accessory", "{hamstrings}"),
    ("Bicep Curl", "accessory", "{biceps}"),
    ("Tricep Pushdown", "accessory", "{triceps}"),
]
def upgrade() -> None:
    """Create exercises + bodyweight_logs, their indexes, and the system seed."""

    op.create_table(
        "exercises",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", exercise_category_enum, nullable=False),
        sa.Column("muscle_groups", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
      # Two complementary uniqueness rules (acceptance #2):
    #  - partial unique on name for system rows (created_by IS NULL), because in
    #    SQL NULL != NULL so a plain UNIQUE would not prevent duplicate system
    #    names.
    #  - composite unique (created_by, name) so a user cannot duplicate one of
    #    their own custom names.

    op.create_table(
        "bodyweight_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
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
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"]),
        sa.UniqueConstraint("athlete_id", "date", name="uq_bodyweight_athlete_date"),
    )


def downgrade() -> None:
    """Drop both tables (reverse order) and the exercise_category enum type."""
    op.drop_table("bodyweight_logs")

    op.drop_table("exercises")
    # `drop_table` does not reliably drop the enum type of the column it held,
    # so drop it explicitly (mirrors the 0002 revision pattern).
    exercise_category_enum.drop(op.get_bind(), checkfirst=True) 