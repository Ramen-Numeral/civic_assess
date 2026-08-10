import asyncio
import math
from collections.abc import Sequence


class EvidenceEmbedder:
    def __init__(
        self,
        model_id: str,
        revision: str,
        *,
        batch_size: int = 32,
        device: str = "cpu",
    ) -> None:
        from sentence_transformers import SentenceTransformer

        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._model = SentenceTransformer(model_id, revision=revision, device=device)
        self.dimension = self._model.get_embedding_dimension()
        if self.dimension is None:
            raise ValueError("Embedding model does not declare its dimension")
        self.version = f"{model_id}@{revision}:document:normalized-float32"
        self.batch_size = batch_size

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return await asyncio.to_thread(self._embed, list(texts), False)

    async def embed_queries(
        self, texts: Sequence[str]
    ) -> tuple[tuple[float, ...], ...]:
        return await asyncio.to_thread(self._embed, list(texts), True)

    def _embed(
        self, texts: list[str], queries: bool
    ) -> tuple[tuple[float, ...], ...]:
        tokens = self._model.tokenizer(texts, truncation=False)["input_ids"]
        if any(len(row) > self._model.max_seq_length for row in tokens):
            raise ValueError("Evidence chunk exceeds the embedding model token limit")
        vectors = tuple(
            tuple(vector)
            for vector in (
                self._model.encode_query if queries else self._model.encode_document
            )(
                texts,
                batch_size=self.batch_size,
                convert_to_numpy=True,
                normalize_embeddings=True,
                precision="float32",
                show_progress_bar=False,
            )
        )
        if len(vectors) != len(texts) or any(not self._valid(row) for row in vectors):
            raise RuntimeError("Embedding model returned invalid vectors")
        return vectors

    def _valid(self, vector: Sequence[float]) -> bool:
        return (
            len(vector) == self.dimension
            and all(math.isfinite(value) for value in vector)
            and 0.99 <= math.sqrt(sum(value * value for value in vector)) <= 1.01
        )
