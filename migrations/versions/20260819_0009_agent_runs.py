"""create agent_runs + agent_run_status, add training_weeks.proposal_id

Revision ID: 20260819_0009
Revises: 20260731_0008
Create Date: 2026-08-19

Ownership boundary (D11-7, R0-B): LangGraph owns the `checkpoints`,
`checkpoint_blobs`, `checkpoint_writes` and `checkpoint_migrations` tables via
`AsyncPostgresSaver.setup()` (run once from `python -m scripts.setup_checkpointer`).
Alembic never creates, alters or drops any `checkpoint*` table — not here, not
anywhere in this migration history. The library ships its own migration ledger
over those tables; two ledgers on one table set is the hazard this boundary
avoids.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260819_0009"
down_revision: str | Sequence[str] | None = "20260731_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `agent_run_status` is NEW and owned solely by `agent_runs` (the opposite of
# `discipline` in 20260731_0008, which is pre-existing and shared): `upgrade()`
# lets `create_table` issue `CREATE TYPE` (no separate `.create()` — an
# explicit create alongside `create_type=True` double-issues it, yata-0002
# finding), and `downgrade()` must drop the type explicitly, because
# `op.drop_table` does not reliably issue `DROP TYPE` on this toolchain
# (yata-0003 finding).
agent_run_status_enum = sa.Enum(
    "running",
    "awaiting_gate",
    "accepted",
    "rejected",
    "failed",
    name="agent_run_status",
)

_PROPOSAL_ID_FK = "fk_training_weeks_proposal_id_agent_runs"


def upgrade() -> None:
    """Create `agent_runs`, then add `training_weeks.proposal_id` (nullable)."""
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("athlete_id", sa.Integer(), nullable=False),
        sa.Column("block_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False, unique=True),
        sa.Column("status", agent_run_status_enum, nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("chunks_retrieved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validation_verdict", sa.Text(), nullable=False),
        sa.Column(
            "validation_errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("proposal", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["block_id"], ["blocks.id"]),
    )

    op.add_column(
        "training_weeks", sa.Column("proposal_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        _PROPOSAL_ID_FK,
        "training_weeks",
        "agent_runs",
        ["proposal_id"],
        ["id"],
    )


def downgrade() -> None:
    """Drop the named FK -> the column -> `agent_runs` -> the enum, in order.

    This order matters: dropping `agent_runs` before the FK/column trips the
    foreign key.
    """
    op.drop_constraint(_PROPOSAL_ID_FK, "training_weeks", type_="foreignkey")
    op.drop_column("training_weeks", "proposal_id")
    op.drop_table("agent_runs")

    bind = op.get_bind()
    agent_run_status_enum.drop(bind, checkfirst=True)
