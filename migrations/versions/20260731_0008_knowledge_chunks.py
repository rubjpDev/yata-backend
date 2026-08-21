"""create knowledge_chunks (RAG corpus with pgvector embeddings)

Revision ID: 20260731_0008
Revises: 20260729_0007
Create Date: 2026-07-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260731_0008"
down_revision: str | Sequence[str] | None = "20260729_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The `discipline` type already exists (created by 0002, still owned by
# `users.discipline`). Use the PostgreSQL-specific `postgresql.ENUM` (not the
# generic `sa.Enum`, which drops `create_type` and silently re-issues CREATE
# TYPE on `create_table`) so this migration creates no type at all.
discipline_enum = postgresql.ENUM("powerlifting", name="discipline", create_type=False)


def upgrade() -> None:
    """Create `knowledge_chunks`: one table, no new type, no vector index."""
    # No vector index on purpose. At the corpus size in play (order of a few
    # hundred chunks) an exact `<=>` sequential scan is sub-millisecond, while
    # HNSW costs build time, gives up some recall, and only starts paying above
    # the order of 10k rows.
    # Ceiling: an HNSW index with `vector_cosine_ops` the day the corpus passes
    # ~10k chunks.
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "discipline",
            discipline_enum,
            nullable=False,
            server_default="powerlifting",
        ),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("source_note", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop `knowledge_chunks` and nothing else."""
    # Deliberately NOT dropped here:
    # - the `discipline` enum type: `users.discipline` still owns it, dropping
    #   it would break the users table;
    # - the `vector` extension: `20260621_0006` owns it.
    op.drop_table("knowledge_chunks")
