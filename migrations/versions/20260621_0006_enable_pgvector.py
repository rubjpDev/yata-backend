"""enable pgvector extension

Revision ID: 20260621_0006
Revises: 20260619_0005
Create Date: 2026-06-21

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic
revision: str = "20260621_0006"
down_revision: str | Sequence[str] | None = "20260619_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enable the pgvector extension (idempotent)"""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    """Disable the pgvector extension (reversible)"""
    op.execute("DROP EXTENSION IF EXISTS vector")
