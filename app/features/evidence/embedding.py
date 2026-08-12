import asyncio
import math
from collections.abc import Sequence
from threading import Lock
from typing import Any


class EvidenceEmbedder:
    def __init__(
        self,
        model_id: str,
        revision: str,
        *,
        batch_size: int = 32,
        device: str = "cpu",
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._model_id = model_id
        self._revision = revision
        self._device = device
        self._model: Any = None
        self._dimension: int | None = None
        self._load_lock = Lock()
        self.tokenization_version = f"{model_id}@{revision}"
        self.version = f"{model_id}@{revision}:document:normalized-float32"
        self.batch_size = batch_size

    @property
    def dimension(self) -> int:
        self._load()
        assert self._dimension is not None
        return self._dimension

    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        encoded = self._load().tokenizer(
            text,
            add_special_tokens=False,
            return_offsets_mapping=True,
            truncation=False,
            verbose=False,
        )
        offsets = encoded.get("offset_mapping")
        if offsets is None:
            raise ValueError("Embedding tokenizer does not provide offset mappings")
        return tuple((int(start), int(end)) for start, end in offsets if end > start)

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return await asyncio.to_thread(self._embed, list(texts), False)

    async def embed_queries(
        self, texts: Sequence[str]
    ) -> tuple[tuple[float, ...], ...]:
        return await asyncio.to_thread(self._embed, list(texts), True)

    def _embed(self, texts: list[str], queries: bool) -> tuple[tuple[float, ...], ...]:
        model = self._load()
        tokens = model.tokenizer(texts, truncation=False)["input_ids"]
        if any(len(row) > model.max_seq_length for row in tokens):
            raise ValueError("Evidence chunk exceeds the embedding model token limit")
        vectors = tuple(
            tuple(vector)
            for vector in (model.encode_query if queries else model.encode_document)(
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

    def _load(self) -> Any:
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    model = SentenceTransformer(
                        self._model_id,
                        revision=self._revision,
                        device=self._device,
                    )
                    dimension = model.get_embedding_dimension()
                    if dimension is None:
                        raise ValueError(
                            "Embedding model does not declare its dimension"
                        )
                    self._model = model
                    self._dimension = dimension
        return self._model

    def _valid(self, vector: Sequence[float]) -> bool:
        return (
            len(vector) == self.dimension
            and all(math.isfinite(value) for value in vector)
            and 0.99 <= math.sqrt(sum(value * value for value in vector)) <= 1.01
        )
