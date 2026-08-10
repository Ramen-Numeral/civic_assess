import asyncio
import heapq
import math
from uuid import UUID

from app.domain.research import ResearchQuerySet
from app.features.evidence_ingestion.embedder import EvidenceEmbedder
from app.features.evidence_ingestion.repository import EvidenceRepository
from app.features.evidence_retrieval.schemas import (
    EvidenceRetrievalSet,
    QueryEvidenceRetrieval,
    ScoredEvidenceCandidate,
)
from app.features.evidence_retrieval.ranking import finalize_candidates


class EvidenceRetrievalService:
    def __init__(
        self,
        repository: EvidenceRepository,
        embedder: EvidenceEmbedder,
        *,
        lexical_candidate_count: int = 20,
        semantic_candidate_count: int = 20,
        rrf_k: int = 60,
        lexical_weight: float = 1.0,
        semantic_weight: float = 1.0,
        coverage_candidate_count: int = 12,
        max_chunks_per_document: int = 2,
    ) -> None:
        if any(value < 1 for value in (
            lexical_candidate_count, semantic_candidate_count, rrf_k,
            coverage_candidate_count, max_chunks_per_document,
        )):
            raise ValueError("Evidence retrieval counts must be positive")
        if any(not math.isfinite(value) or value < 0 for value in (
            lexical_weight, semantic_weight,
        )) or not (lexical_weight or semantic_weight):
            raise ValueError("At least one finite retrieval weight must be positive")
        self._repository = repository
        self._embedder = embedder
        self._lexical_count = lexical_candidate_count
        self._semantic_count = semantic_candidate_count
        self._rrf_k = rrf_k
        self._lexical_weight = lexical_weight
        self._semantic_weight = semantic_weight
        self._coverage_count = coverage_candidate_count
        self._document_limit = max_chunks_per_document

    async def retrieve(
        self,
        *,
        conversation_id: UUID,
        query_set: ResearchQuerySet,
    ) -> EvidenceRetrievalSet:
        queries = (query_set.original_query, *query_set.diversified_queries)
        lexical_results, semantic_results = await asyncio.gather(
            self._lexical(conversation_id, queries),
            self._semantic(conversation_id, queries),
        )
        results = []
        for query, lexical, semantic in zip(
            queries, lexical_results, semantic_results, strict=True
        ):
            results.append(QueryEvidenceRetrieval(
                query_id=query.query_id,
                query_text=query.text,
                lexical=lexical,
                semantic=semantic,
            ))
        query_results = tuple(results)
        return EvidenceRetrievalSet(
            conversation_id=conversation_id,
            embedding_version=(
                self._embedder.version if self._semantic_weight else None
            ),
            query_results=query_results,
            ranked_candidates=finalize_candidates(
                query_results,
                rrf_k=self._rrf_k,
                lexical_weight=self._lexical_weight,
                semantic_weight=self._semantic_weight,
                limit=self._coverage_count,
                document_limit=self._document_limit,
            ),
        )

    async def _lexical(self, conversation_id, queries):
        if not self._lexical_weight:
            return ((),) * len(queries)
        return await asyncio.gather(*(
            self._repository.search_evidence_text(
                conversation_id, query.text, self._lexical_count
            )
            for query in queries
        ))

    async def _semantic(self, conversation_id, queries):
        if not self._semantic_weight:
            return ((),) * len(queries)
        vectors, corpus = await asyncio.gather(
            self._embedder.embed_queries([query.text for query in queries]),
            self._repository.load_evidence_vectors(
                conversation_id, self._embedder.version
            ),
        )
        return tuple(
            tuple(
                ScoredEvidenceCandidate(
                    evidence=evidence,
                    rank=rank,
                    score=_cosine(query_vector, vector),
                )
                for rank, (evidence, vector) in enumerate(
                    heapq.nsmallest(
                        self._semantic_count,
                        corpus,
                        key=lambda row: (
                            -_cosine(query_vector, row[1]),
                            str(row[0].chunk_id),
                        ),
                    ),
                    start=1,
                )
            )
            for query_vector in vectors
        )


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise RuntimeError("Stored evidence vector has an incompatible dimension")
    return float(sum(a * b for a, b in zip(left, right, strict=True)))
