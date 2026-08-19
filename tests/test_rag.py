"""SQLite/offline tests: the pure chunker, the corpus parser and `--dry-run`.

Nothing here touches PostgreSQL and nothing here loads an embedding model.
"""

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import EmbeddingClient
from app.models import KnowledgeChunk
from app.rag import chunk_markdown
from scripts import ingest_corpus
from scripts.ingest_corpus import CorpusError, hash_chunk, parse_front_matter

_DOC = """# Autoregulation

RPE rates the set you finished.

## Reporting honestly

Round to the half point.

### Edge cases

A missed rep is not an RPE 10.
"""


def test_chunk_markdown_returns_one_chunk_per_heading_section() -> None:
    chunks = chunk_markdown(_DOC, 1000)

    assert len(chunks) == 3
    assert chunks[0].startswith("# Autoregulation")
    assert chunks[1].startswith("## Reporting honestly")
    assert chunks[2].startswith("### Edge cases")


def test_chunk_markdown_preserves_document_order() -> None:
    chunks = chunk_markdown(_DOC, 1000)

    positions = [_DOC.index(chunk.splitlines()[0]) for chunk in chunks]
    assert positions == sorted(positions)


def test_chunk_markdown_splits_an_over_long_section_without_overlap() -> None:
    body = "\n".join(f"line {index} of a long section" for index in range(60))
    text = f"# Long\n\n{body}\n"

    chunks = chunk_markdown(text, 200)

    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)
    rebuilt = "\n".join(chunks)
    for index in range(60):
        assert rebuilt.count(f"line {index} of a long section") == 1


def test_chunk_markdown_handles_text_without_any_heading() -> None:
    text = "Just a paragraph.\n\nAnd another one.\n"

    chunks = chunk_markdown(text, 1000)

    assert chunks == ["Just a paragraph.\n\nAnd another one."]


def test_chunk_markdown_returns_no_empty_chunk() -> None:
    text = "# One\n\n\n\n## Two\n\n\n"

    chunks = chunk_markdown(text, 1000)

    assert chunks == ["# One", "## Two"]


@pytest.mark.parametrize("max_chars", [0, -1])
def test_chunk_markdown_rejects_a_non_positive_max_chars(max_chars: int) -> None:
    with pytest.raises(ValueError, match="max_chars"):
        chunk_markdown("# Heading\n\nbody\n", max_chars)


class FakeEmbedder:
    """Deterministic `EmbeddingClient`: a hash of the text, no model, no network."""

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        # SHA-256 gives 32 bytes, so 12 rounds cover the 384 dimensions. Built
        # from a hash, not `hash()`, so the vectors are stable across processes.
        raw = b"".join(
            hashlib.sha256(f"{index}:{text}".encode()).digest() for index in range(12)
        )
        return [byte / 255.0 for byte in raw]


def test_fake_embedder_is_deterministic_and_384_dimensional() -> None:
    embedder = FakeEmbedder()

    first = embedder.embed_query("how do I deload")
    second = embedder.embed_query("how do I deload")
    other = embedder.embed_query("how do I pick accessories")

    assert len(first) == 384
    assert first == second
    assert first != other


def test_fake_embedder_satisfies_the_embedding_client_protocol() -> None:
    client: EmbeddingClient = FakeEmbedder()

    assert len(client.embed_passages(["a", "b"])) == 2


_VALID_FILE = """---
topic: deload_criteria
source_note: Original prose written for YATA by the repo owner.
---

# When to deload

Three flat sessions in a row.
"""


def test_parse_front_matter_returns_the_keys_and_the_body() -> None:
    meta, body = parse_front_matter(_VALID_FILE)

    assert meta["topic"] == "deload_criteria"
    assert meta["source_note"].startswith("Original prose")
    assert body.lstrip().startswith("# When to deload")
    assert "topic:" not in body


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# No front matter\n\nbody\n", "front-matter"),
        ("---\ntopic: deload_criteria\n---\n\nbody\n", "source_note"),
        (_VALID_FILE.replace("deload_criteria", "deload_criteriaa"), "unknown topic"),
    ],
)
def test_parse_front_matter_refuses_a_corpus_file_it_cannot_trust(
    text: str, expected: str
) -> None:
    with pytest.raises(CorpusError, match=expected):
        parse_front_matter(text)


def test_hash_chunk_is_stable_across_calls() -> None:
    assert hash_chunk("## Deload\n\ncut volume") == hash_chunk(
        "## Deload\n\ncut volume"
    )
    assert hash_chunk("a") != hash_chunk("b")


def test_chunks_for_file_names_the_offending_file(tmp_path: Path) -> None:
    bad = tmp_path / "09-broken.md"
    bad.write_text("# no front matter\n", encoding="utf-8")

    with pytest.raises(CorpusError, match="09-broken"):
        ingest_corpus.chunks_for_file(bad)


async def test_dry_run_writes_nothing_and_never_builds_an_embedding_client(
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode() -> object:
        raise AssertionError("--dry-run must not build the embedding client")

    monkeypatch.setattr(ingest_corpus, "get_embedding_client", _explode)

    exit_code = ingest_corpus.main(["--dry-run"])

    assert exit_code == 0
    assert "rows inserted: 0" in capsys.readouterr().out
    assert (
        await db_session.scalar(select(func.count()).select_from(KnowledgeChunk)) == 0
    )


async def test_knowledge_chunks_table_exists_on_sqlite(
    db_session: AsyncSession,
) -> None:
    """The `with_variant` guarantee: `create_all` builds the table on SQLite."""
    assert (
        await db_session.scalar(select(func.count()).select_from(KnowledgeChunk)) == 0
    )
