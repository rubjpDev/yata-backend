"""create training plan tables (blocks, training_weeks, training_sessions, sets)

Revision ID: 20260729_0007
Revises: 20260621_0006
Create Date: 2026-07-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260729_0007"
down_revision: str | Sequence[str] | None = "20260621_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

block_intent_enum = sa.Enum(
    "accumulation", "intensification", "peak", "deload", "general", name="block_intent"
)
block_status_enum = sa.Enum("active", "completed", "abandoned", name="block_status")
week_status_enum = sa.Enum("proposed", "active", "completed", name="week_status")
session_type_enum = sa.Enum(
    "squat", "bench", "deadlift", "upper", "lower", "full", name="session_type"
)
session_status_enum = sa.Enum(
    "prescribed", "partial", "completed", name="session_status"
)
set_type_enum = sa.Enum("warmup", "working", "backoff", name="set_type")
intensity_type_enum = sa.Enum("RPE", "RIR", name="intensity_type")
weight_mode_enum = sa.Enum("fixed", "free", name="weight_mode")


def upgrade() -> None:
    """Create the four training-plan tables (FK order) with their enums inline.

    Enums are declared on the columns and left to `create_table` to
    `CREATE TYPE` (default `create_type=True`); do not pre-`.create()` them,
    that double-issues the type (knowledge-pack yata-0002).
    """
    op.create_table(
        "blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("intent", block_intent_enum, nullable=False),
        sa.Column("planned_weeks", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("status", block_status_enum, nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"]),
    )
    op.create_index("ix_blocks_athlete_id", "blocks", ["athlete_id"])

    op.create_table(
        "training_weeks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("block_id", sa.Integer(), nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("week_index", sa.Integer(), nullable=False),
        sa.Column("days_planned", sa.Integer(), nullable=False),
        sa.Column("status", week_status_enum, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["block_id"], ["blocks.id"]),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"]),
    )
    op.create_index("ix_training_weeks_athlete_id", "training_weeks", ["athlete_id"])
    op.create_index("ix_training_weeks_block_id", "training_weeks", ["block_id"])

    op.create_table(
        "training_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("week_id", sa.Integer(), nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("session_type", session_type_enum, nullable=False),
        sa.Column(
            "status",
            session_status_enum,
            nullable=False,
            server_default="prescribed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["week_id"], ["training_weeks.id"]),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"]),
    )
    op.create_index(
        "ix_training_sessions_athlete_id", "training_sessions", ["athlete_id"]
    )
    op.create_index("ix_training_sessions_week_id", "training_sessions", ["week_id"])

    op.create_table(
        "sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("exercise_id", sa.Integer(), nullable=False),
        sa.Column("set_order", sa.Integer(), nullable=False),
        sa.Column("set_type", set_type_enum, nullable=False),
        sa.Column("intensity_type", intensity_type_enum, nullable=False),
        sa.Column("weight_mode", weight_mode_enum, nullable=False),
        sa.Column(
            "equipment_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("prescribed_weight_kg", sa.Float(), nullable=True),
        sa.Column("prescribed_reps", sa.Integer(), nullable=True),
        sa.Column("prescribed_intensity", sa.Float(), nullable=True),
        sa.Column("executed_weight_kg", sa.Float(), nullable=True),
        sa.Column("executed_reps", sa.Integer(), nullable=True),
        sa.Column("executed_intensity", sa.Float(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["training_sessions.id"]),
        sa.ForeignKeyConstraint(["athlete_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["exercise_id"], ["exercises.id"]),
    )
    op.create_index("ix_sets_athlete_id", "sets", ["athlete_id"])
    op.create_index("ix_sets_session_id", "sets", ["session_id"])


def downgrade() -> None:
    """Drop the four tables (reverse order), then the 8 enum types explicitly.

    `op.drop_table` does not reliably `DROP TYPE` for column-declared enums on
    this toolchain (knowledge-pack yata-0003); without the explicit drop below
    the next upgrade fails with `DuplicateObject`.
    """
    op.drop_table("sets")
    op.drop_table("training_sessions")
    op.drop_table("training_weeks")
    op.drop_table("blocks")

    bind = op.get_bind()
    weight_mode_enum.drop(bind, checkfirst=True)
    intensity_type_enum.drop(bind, checkfirst=True)
    set_type_enum.drop(bind, checkfirst=True)
    session_status_enum.drop(bind, checkfirst=True)
    session_type_enum.drop(bind, checkfirst=True)
    week_status_enum.drop(bind, checkfirst=True)
    block_status_enum.drop(bind, checkfirst=True)
    block_intent_enum.drop(bind, checkfirst=True)
