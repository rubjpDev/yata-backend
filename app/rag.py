"""RAG layer: a pure markdown chunker plus pgvector-backed retrieval."""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import EmbeddingClient
from app.models import KnowledgeChunk


def chunk_markdown(text: str, max_chars: int) -> list[str]:
    """Split markdown into chunks of at most `max_chars`, one per heading section.

    A section starts at a `#` heading line and runs to the line before the next
    heading of any level, so every chunk carries its own heading as context.
    """
    if max_chars < 1:
        raise ValueError(f"max_chars must be at least 1, got {max_chars}")

    sections: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("#") or not sections:
            sections.append([])
        sections[-1].append(line)

    chunks: list[str] = []
    for lines in sections:
        chunks.extend(_split_section(lines, max_chars))

    return chunks


def _split_section(lines: list[str], max_chars: int) -> list[str]:
    """Pack a section's lines into consecutive pieces of at most `max_chars`."""
    pieces: list[str] = []
    current: list[str] = []
    length = 0

    for line in lines:
        for part in _hard_wrap(line, max_chars):
            added = len(part) if not current else len(part) + 1
            if current and length + added > max_chars:
                pieces.append("\n".join(current))
                current = []
                length = 0
                added = len(part)
            current.append(part)
            length += added

    if current:
        pieces.append("\n".join(current))

    cleaned = [piece.strip() for piece in pieces]
    return [piece for piece in cleaned if piece]


def _hard_wrap(line: str, max_chars: int) -> list[str]:
    """Cut one over-long line so the `max_chars` cap can hold unconditionally."""
    if len(line) <= max_chars:
        return [line]
    return [line[start : start + max_chars] for start in range(0, len(line), max_chars)]


async def retrieve(
    session: AsyncSession,
    query: str,
    embedder: EmbeddingClient,
    *,
    topic: str | None = None,
    k: int = 5,
) -> list[KnowledgeChunk]:
    """Return the `k` chunks nearest to `query` by cosine distance, nearest first.

    Requires PostgreSQL: the ordering is computed by pgvector in the database,
    so this function is exercised only by the `pg`-marked tests.
    """
    # Refuse before embedding: embedding is the expensive half of this call.
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")

    vector = embedder.embed_query(query)

    # The column is declared with a `with_variant`, so its Python-side type is
    # `LargeBinary` and carries no vector comparator; `type_coerce` supplies one
    # without emitting any SQL.
    distance = sa.type_coerce(KnowledgeChunk.embedding, Vector(384)).cosine_distance(
        vector
    )

    statement = select(KnowledgeChunk)
    if topic is not None:
        statement = statement.where(KnowledgeChunk.topic == topic)
    statement = statement.order_by(distance).limit(k)

    result = await session.execute(statement)
    return list(result.scalars().all())
