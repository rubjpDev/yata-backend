"""Embedding clients: the injectable interface and its fastembed implementation."""

from collections.abc import Sequence
from typing import Protocol

from fastembed import TextEmbedding

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384


class EmbeddingClient(Protocol):
    """Turns text into 384-float vectors: one path for passages, one for queries."""

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed corpus passages, in the order they were given."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query."""
        ...


class FastEmbedClient:
    """`EmbeddingClient` backed by fastembed running BGE on onnxruntime."""

    def __init__(self, model_name: str = EMBEDDING_MODEL) -> None:
        self._model_name = model_name
        self._model: TextEmbedding | None = None

    def _get_model(self) -> TextEmbedding:
        """Build the model on first use: constructing it downloads ~130 MB."""
        if self._model is None:
            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed corpus passages through the model's passage path."""
        vectors = self._get_model().embed(list(texts))
        return [vector.tolist() for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query through the model's query path."""
        # BGE is trained asymmetrically: `query_embed` applies the instruction
        # prefix the model saw on queries in training. Using `embed` here raises
        # nothing and silently costs recall.
        vectors = list(self._get_model().query_embed(text))
        return [float(value) for value in vectors[0]]
