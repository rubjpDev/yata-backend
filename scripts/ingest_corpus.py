"""Ingest `docs/corpus/*.md` into `knowledge_chunks`, idempotently.

Run as `python -m scripts.ingest_corpus [--dry-run]` from the repo root.
"""

import argparse
import asyncio
import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal
from app.deps import get_embedding_client
from app.embeddings import EmbeddingClient
from app.models import Discipline, KnowledgeChunk
from app.rag import chunk_markdown

CORPUS_DIR = Path(__file__).resolve().parent.parent / "docs" / "corpus"

# ponytail: a constant, not a flag and not a setting — it does not change per
# run. Ceiling: a parameter the day two corpora need two values.
MAX_CHARS = 1200

VALID_TOPICS = frozenset(
    {
        "rpe_autoregulation",
        "rpe_vs_rir",
        "volume_landmarks",
        "deload_criteria",
        "exercise_selection",
        "block_periodization",
        "equipment_periodization",
        "e1rm_limits",
    }
)

REQUIRED_KEYS = ("topic", "source_note")


class CorpusError(Exception):
    """A corpus file that cannot be trusted; the ingest refuses to write."""


@dataclass(frozen=True)
class PendingChunk:
    """One chunk ready to be embedded and inserted."""

    topic: str
    source_note: str
    content: str
    content_hash: str


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split a corpus file into its front-matter mapping and its markdown body.

    Trust boundary: every deviation from the documented format refuses instead
    of guessing, because a half-valid corpus makes the retrieval evals lie.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise CorpusError("missing front-matter: the first line must be '---'")

    meta: dict[str, str] = {}
    body = ""
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body = "\n".join(lines[index + 1 :])
            break
        key, separator, value = line.partition(":")
        if not separator:
            raise CorpusError(f"front-matter line is not 'key: value': {line!r}")
        meta[key.strip()] = value.strip()
    else:
        raise CorpusError("front-matter block is never closed by a '---' line")

    for key in REQUIRED_KEYS:
        if not meta.get(key):
            raise CorpusError(f"front-matter is missing '{key}'")
    if meta["topic"] not in VALID_TOPICS:
        raise CorpusError(f"unknown topic: {meta['topic']!r}")

    return meta, body


def hash_chunk(text: str) -> str:
    """SHA-256 hexdigest of a chunk: the identity idempotency rests on."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def corpus_files(corpus_dir: Path) -> list[Path]:
    """The corpus files in the deterministic order the ingest processes them."""
    return sorted(corpus_dir.glob("*.md"))


def chunks_for_file(path: Path) -> list[PendingChunk]:
    """Parse, chunk and hash one corpus file, naming it on any failure."""
    try:
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
    except CorpusError as error:
        # The file name is the only debugging aid the operator gets.
        raise CorpusError(f"{path.name}: {error}") from error

    return [
        PendingChunk(
            topic=meta["topic"],
            source_note=meta["source_note"],
            content=chunk,
            content_hash=hash_chunk(chunk),
        )
        for chunk in chunk_markdown(body, MAX_CHARS)
    ]


async def ingest(
    session: AsyncSession,
    pending: Sequence[PendingChunk],
    embedder: EmbeddingClient,
) -> tuple[int, int]:
    """Insert the chunks whose hash is new; return `(inserted, skipped)`."""
    result = await session.execute(
        select(KnowledgeChunk.content_hash).where(
            KnowledgeChunk.content_hash.in_([chunk.content_hash for chunk in pending])
        )
    )
    seen = set(result.scalars().all())

    # Deduplicating against the run itself too: two identical sections in the
    # corpus would otherwise hit the `content_hash` UNIQUE constraint.
    fresh: list[PendingChunk] = []
    for chunk in pending:
        if chunk.content_hash in seen:
            continue
        seen.add(chunk.content_hash)
        fresh.append(chunk)

    if not fresh:
        return 0, len(pending)

    # Skipped chunks are never embedded: embedding is the expensive step, and
    # skipping it is what makes a re-run nearly free.
    vectors = embedder.embed_passages([chunk.content for chunk in fresh])
    session.add_all(
        [
            KnowledgeChunk(
                discipline=Discipline.powerlifting,
                topic=chunk.topic,
                source_note=chunk.source_note,
                content=chunk.content,
                content_hash=chunk.content_hash,
                embedding=vector,
            )
            for chunk, vector in zip(fresh, vectors, strict=True)
        ]
    )
    await session.commit()
    return len(fresh), len(pending) - len(fresh)


async def _run_ingest(pending: Sequence[PendingChunk]) -> tuple[int, int]:
    """Open a session and ingest with the process-wide embedding client."""
    async with AsyncSessionLocal() as session:
        return await ingest(session, pending, get_embedding_client())


def _summary(files: int, chunks: int, inserted: int, skipped: int) -> str:
    """The one-line run summary; the script's only success output."""
    return (
        f"files read: {files} | chunks produced: {chunks} | "
        f"rows inserted: {inserted} | rows skipped: {skipped}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: parse args, collect chunks, ingest unless `--dry-run`."""
    parser = argparse.ArgumentParser(
        description="Ingest docs/corpus/*.md into knowledge_chunks."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse, chunk and hash only: no embedding client, no writes",
    )
    args = parser.parse_args(argv)

    files = corpus_files(CORPUS_DIR)
    if not files:
        print(f"ingest failed: no corpus files in {CORPUS_DIR}", file=sys.stderr)
        return 1

    pending: list[PendingChunk] = []
    for path in files:
        try:
            file_chunks = chunks_for_file(path)
        except CorpusError as error:
            print(f"ingest failed: {error}", file=sys.stderr)
            return 1
        print(f"{path.name}: {len(file_chunks)} chunks")
        pending.extend(file_chunks)

    if args.dry_run:
        print(_summary(len(files), len(pending), inserted=0, skipped=0))
        return 0

    try:
        inserted, skipped = asyncio.run(_run_ingest(pending))
    except Exception as error:
        print(f"ingest failed: {error}", file=sys.stderr)
        return 1

    print(_summary(len(files), len(pending), inserted, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
