import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid5

from app.domain.acquisition import (
    AcquiredSearchResult,
    QueryAcquisitionSuccess,
    ResearchAcquisitionSet,
)
from app.domain.evidence import (
    EvidenceDiscovery,
    EvidenceDocument,
    EvidenceIngestionBatch,
    EvidenceIngestionSnapshot,
    EvidenceQueryKind,
    EvidenceResearchQuery,
)
from app.domain.research import ResearchQuerySet
from app.features.evidence_ingestion.canonicalization import canonicalize_url
from app.features.evidence_ingestion.chunker import MarkdownChunker
from app.features.evidence_ingestion.errors import EvidenceIngestionError
from app.features.evidence_ingestion.repository import (
    EvidenceRepository,
    EvidenceWriteStatus,
)


DOCUMENT_NAMESPACE = UUID("7afe3980-f2a6-47f8-8310-1d2f9faf12dc")


class EvidenceIngestionService:
    def __init__(
        self,
        repository: EvidenceRepository,
        chunker: MarkdownChunker,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._chunker = chunker
        self._now = now or (lambda: datetime.now(UTC))

    async def ingest(
        self,
        *,
        conversation_id: UUID,
        query_set: ResearchQuerySet,
        acquisition: ResearchAcquisitionSet,
        round_number: int = 0,
    ) -> EvidenceIngestionSnapshot:
        queries = _queries(query_set)
        expected = [(query.query_id, query.text) for query in queries]
        actual = [
            (outcome.query_id, outcome.query_text)
            for outcome in acquisition.outcomes
        ]
        if actual != expected:
            raise EvidenceIngestionError(
                "invalid_acquisition",
                "Acquisition queries do not match the research query set",
            )

        grouped: dict[
            str,
            list[tuple[int, QueryAcquisitionSuccess, AcquiredSearchResult]],
        ] = {}
        skipped: list[UUID] = []
        for query_position, outcome in enumerate(acquisition.outcomes):
            if not isinstance(outcome, QueryAcquisitionSuccess):
                continue
            for result in outcome.results:
                canonical_url = canonicalize_url(str(result.url))
                grouped.setdefault(canonical_url, []).append(
                    (query_position, outcome, result)
                )
                if result.raw_content is None:
                    skipped.append(result.result_id)

        documents: list[EvidenceDocument] = []
        chunks = []
        for canonical_url, entries in grouped.items():
            usable = [entry for entry in entries if entry[2].raw_content is not None]
            if not usable:
                continue
            winner = min(
                usable,
                key=lambda entry: (
                    -len(entry[2].raw_content),
                    entry[0],
                    entry[2].rank,
                ),
            )
            content = winner[2].raw_content
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            document_id = uuid5(
                DOCUMENT_NAMESPACE,
                f"{conversation_id}:{canonical_url}:{content_hash}",
            )
            document = EvidenceDocument(
                document_id=document_id,
                conversation_id=conversation_id,
                canonical_url=canonical_url,
                title=winner[2].title,
                raw_content=content,
                content_hash=content_hash,
                acquired_at=acquisition.acquired_at,
                discoveries=tuple(
                    EvidenceDiscovery(
                        query_id=outcome.query_id,
                        result_id=result.result_id,
                        rank=result.rank,
                        original_url=result.url,
                        provider_result_id=result.provider_result_id,
                    )
                    for _, outcome, result in entries
                ),
            )
            documents.append(document)
            chunks.extend(self._chunker.chunk(document_id, content))

        if not documents:
            raise EvidenceIngestionError(
                "no_extractable_evidence",
                "Research acquisition returned no extractable evidence",
            )

        fingerprint = hashlib.sha256(
            (
                query_set.model_dump_json()
                + acquisition.model_dump_json()
                + str(round_number)
            ).encode()
        ).hexdigest()
        batch = EvidenceIngestionBatch(
            acquisition_id=acquisition.acquisition_id,
            conversation_id=conversation_id,
            round_number=round_number,
            fingerprint=fingerprint,
            ingested_at=self._now(),
            queries=tuple(queries),
            documents=tuple(documents),
            chunks=tuple(chunks),
            skipped_result_ids=tuple(skipped),
        )
        result = await self._repository.write_evidence(batch)
        if result.status is EvidenceWriteStatus.MISSING:
            raise EvidenceIngestionError(
                "conversation_missing",
                "Conversation does not exist",
            )
        if result.status is EvidenceWriteStatus.CONFLICT:
            raise EvidenceIngestionError(
                "ingestion_conflict",
                "Acquisition identity was already used for different evidence",
            )
        if result.snapshot is None:
            raise RuntimeError("Evidence repository returned no snapshot")
        return result.snapshot


def _queries(query_set: ResearchQuerySet) -> list[EvidenceResearchQuery]:
    result = [EvidenceResearchQuery(
        query_id=query_set.original_query.query_id,
        position=0,
        kind=EvidenceQueryKind.ORIGINAL,
        text=query_set.original_query.text,
    )]
    result.extend(
        EvidenceResearchQuery(
            query_id=query.query_id,
            position=position,
            kind=EvidenceQueryKind.DIVERSIFIED,
            text=query.text,
            facet=query.facet,
            research_goal=query.research_goal,
        )
        for position, query in enumerate(query_set.diversified_queries, start=1)
    )
    return result
