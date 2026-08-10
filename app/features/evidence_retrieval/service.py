import heapq
from uuid import UUID

from app.domain.research import ResearchQuerySet
from app.features.evidence_ingestion.embedder import EvidenceEmbedder
from app.features.evidence_ingestion.repository import EvidenceRepository
from app.features.evidence_retrieval.schemas import (
    EvidenceRetrievalSet,
    QueryEvidenceRetrieval,
    ScoredEvidenceCandidate,
)


class EvidenceRetrievalService:
    def __init__(
        self,
        repository: EvidenceRepository,
        embedder: EvidenceEmbedder,
        *,
        candidate_limit: int = 10,
    ) -> None:
        if candidate_limit < 1:
            raise ValueError("candidate_limit must be positive")
        self._repository = repository
        self._embedder = embedder
        self._limit = candidate_limit

    async def retrieve(
        self,
        *,
        conversation_id: UUID,
        query_set: ResearchQuerySet,
    ) -> EvidenceRetrievalSet:
        queries = (query_set.original_query, *query_set.diversified_queries)
        query_vectors = await self._embedder.embed_queries(
            [query.text for query in queries]
        )
        corpus = await self._repository.load_evidence_vectors(
            conversation_id, self._embedder.version
        )
        results = []
        for query, query_vector in zip(queries, query_vectors, strict=True):
            lexical = await self._repository.search_evidence_text(
                conversation_id, query.text, self._limit
            )
            best = heapq.nsmallest(
                self._limit,
                corpus,
                key=lambda row: (
                    -_cosine(query_vector, row[1]),
                    str(row[0].chunk_id),
                ),
            )
            semantic = tuple(
                ScoredEvidenceCandidate(
                    evidence=evidence,
                    rank=rank,
                    score=_cosine(query_vector, vector),
                )
                for rank, (evidence, vector) in enumerate(best, start=1)
            )
            results.append(QueryEvidenceRetrieval(
                query_id=query.query_id,
                query_text=query.text,
                lexical=lexical,
                semantic=semantic,
            ))
        return EvidenceRetrievalSet(
            conversation_id=conversation_id,
            embedding_version=self._embedder.version,
            query_results=tuple(results),
        )


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise RuntimeError("Stored evidence vector has an incompatible dimension")
    return float(sum(a * b for a, b in zip(left, right, strict=True)))
