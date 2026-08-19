"""PostgreSQL-only tests: ingest idempotency, `retrieve`, and the retrieval evals.

Skipped unless `YATA_TEST_PG_URL` points at a migrated database with pgvector.
"""

import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.deps import get_embedding_client
from app.models import Discipline, KnowledgeChunk
from app.rag import retrieve
from scripts import ingest_corpus
from tests.test_rag import FakeEmbedder

PG_URL = os.getenv("YATA_TEST_PG_URL", "")

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not PG_URL, reason="set YATA_TEST_PG_URL to run the pg tests"),
]


@pytest_asyncio.fixture
async def pg_session() -> AsyncGenerator[AsyncSession]:
    """A session against the migrated PostgreSQL test database.

    The schema comes from `alembic upgrade head`, never from `create_all`:
    `create_all` here would hide the migration bugs these tests exist to catch.
    """
    engine = create_async_engine(PG_URL)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    await engine.dispose()


async def _count(session: AsyncSession) -> int:
    """Row count of `knowledge_chunks`."""
    return await session.scalar(select(func.count()).select_from(KnowledgeChunk)) or 0


_FIXTURE_FILE = """---
topic: deload_criteria
source_note: pg-test fixture corpus, inserted and removed by this module.
---

# Fixture section

A deload cuts volume roughly in half for one week.

## Second section

Come back at the previous load, not above it.
"""


async def test_ingest_twice_leaves_the_row_count_unchanged(
    pg_session: AsyncSession, tmp_path: Path
) -> None:
    corpus = tmp_path / "99-fixture.md"
    corpus.write_text(_FIXTURE_FILE, encoding="utf-8")
    pending = ingest_corpus.chunks_for_file(corpus)

    try:
        inserted_first, _ = await ingest_corpus.ingest(
            pg_session, pending, FakeEmbedder()
        )
        count_first = await _count(pg_session)

        inserted_second, skipped_second = await ingest_corpus.ingest(
            pg_session, pending, FakeEmbedder()
        )

        assert inserted_first == len(pending)
        assert inserted_second == 0
        assert skipped_second == len(pending)
        assert await _count(pg_session) == count_first
    finally:
        await pg_session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.content_hash.in_(
                    [chunk.content_hash for chunk in pending]
                )
            )
        )
        await pg_session.commit()


_MARKER = "pg-test seed row"


@pytest_asyncio.fixture
async def seeded(pg_session: AsyncSession) -> AsyncGenerator[FakeEmbedder]:
    """Insert four known chunks with the deterministic fake, then remove them."""
    embedder = FakeEmbedder()
    rows = [
        ("deload_criteria", "cut volume in half for one week"),
        ("deload_criteria", "three flat sessions in a row"),
        ("volume_landmarks", "eleven hard squat sets a week"),
        ("e1rm_limits", "estimates degrade above five reps"),
    ]
    vectors = embedder.embed_passages([content for _, content in rows])
    pg_session.add_all(
        [
            KnowledgeChunk(
                discipline=Discipline.powerlifting,
                topic=topic,
                source_note=_MARKER,
                content=content,
                content_hash=ingest_corpus.hash_chunk(f"{_MARKER}:{content}"),
                embedding=vector,
            )
            for (topic, content), vector in zip(rows, vectors, strict=True)
        ]
    )
    await pg_session.commit()

    yield embedder

    await pg_session.execute(
        delete(KnowledgeChunk).where(KnowledgeChunk.source_note == _MARKER)
    )
    await pg_session.commit()


async def test_retrieve_filters_by_topic(
    pg_session: AsyncSession, seeded: FakeEmbedder
) -> None:
    chunks = await retrieve(
        pg_session, "how do I know I need a deload", seeded, topic="deload_criteria"
    )

    assert chunks
    assert {chunk.topic for chunk in chunks} == {"deload_criteria"}


async def test_retrieve_returns_at_most_k_rows(
    pg_session: AsyncSession, seeded: FakeEmbedder
) -> None:
    chunks = await retrieve(pg_session, "volume", seeded, k=2)

    assert len(chunks) <= 2


async def test_retrieve_orders_nearest_first(
    pg_session: AsyncSession, seeded: FakeEmbedder
) -> None:
    # The fake hashes text, so an exact-text query sits at distance 0 from its
    # own row: that row must come back first or the ORDER BY is inverted.
    chunks = await retrieve(pg_session, "three flat sessions in a row", seeded, k=4)

    assert chunks[0].content == "three flat sessions in a row"


async def test_retrieve_returns_empty_list_when_nothing_matches(
    pg_session: AsyncSession, seeded: FakeEmbedder
) -> None:
    chunks = await retrieve(pg_session, "anything", seeded, topic="no_such_topic")

    assert chunks == []


async def test_retrieve_rejects_a_non_positive_k(
    pg_session: AsyncSession, seeded: FakeEmbedder
) -> None:
    with pytest.raises(ValueError, match="k"):
        await retrieve(pg_session, "anything", seeded, k=0)


# Eight natural-language questions, written the way an athlete would ask them,
# each paired with the corpus topic that must answer it.
EVAL_CASES = [
    ("my last set felt way harder than the RPE I planned", "rpe_autoregulation"),
    ("how many reps did I actually leave in the tank", "rpe_vs_rir"),
    ("how many hard sets per week is too many for squats", "volume_landmarks"),
    ("bar speed is down and the same weight feels heavier", "deload_criteria"),
    ("should I do pause squats or just more competition squats", "exercise_selection"),
    (
        "what changes between an accumulation and an intensification block",
        "block_periodization",
    ),
    (
        "when in the block should I start using my belt and knee sleeves",
        "equipment_periodization",
    ),
    ("can I trust an estimated one rep max from a set of eight", "e1rm_limits"),
]


@pytest_asyncio.fixture
async def ingested_corpus(pg_session: AsyncSession) -> None:
    """The evals measure the real ingested corpus, not fixture rows."""
    if await _count(pg_session) == 0:
        pytest.skip("run `python -m scripts.ingest_corpus` before the evals")


@pytest.mark.parametrize(("query", "expected_topic"), EVAL_CASES)
async def test_eval_query_reaches_its_topic_in_the_top_five(
    pg_session: AsyncSession, ingested_corpus: None, query: str, expected_topic: str
) -> None:
    chunks = await retrieve(pg_session, query, get_embedding_client(), topic=None, k=5)

    topics = [chunk.topic for chunk in chunks]
    assert expected_topic in topics, f"{query!r} returned {topics}"
