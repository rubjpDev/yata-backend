"""One-shot: create LangGraph's own `checkpoint*` tables (R0-B, R12).

Run as `python -m scripts.setup_checkpointer` from the repo root, once per
environment, as a deploy-time bootstrap step alongside `alembic upgrade head`
— NOT automatically. Alembic never creates, alters or drops `checkpoints`,
`checkpoint_blobs`, `checkpoint_writes` or `checkpoint_migrations`
(`migrations/versions/20260819_0009_agent_runs.py` says so); LangGraph ships
its own migration ledger over those tables (`checkpoint_migrations`), and two
ledgers on one table set is the hazard this boundary avoids (D11-7). The
application itself never calls `.setup()` — not on startup, not on first
request, not per run (`app/deps.py::get_checkpointer`).
"""

import asyncio

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config import settings


async def main() -> None:
    """Strip the asyncpg driver suffix (psycopg 3 needs a plain DSN) and setup()."""
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
        await checkpointer.setup()
    print("checkpoint* tables ready.")


if __name__ == "__main__":
    asyncio.run(main())
