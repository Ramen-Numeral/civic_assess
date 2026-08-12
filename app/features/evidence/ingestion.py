import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid5

from app.domain.evidence import EvidenceDiscovery, EvidenceDocument
from app.domain.research import (
    AcquiredSearchResult,
    QueryAcquisitionSuccess,
    ResearchAcquisitionSet,
    ResearchQuerySet,
)
from app.features.evidence.canonicalization import canonicalize_url
from app.features.evidence.chunking import MarkdownChunker
from app.features.evidence.embedding import EvidenceEmbedder
from app.features.evidence.errors import EvidenceIngestionError
from app.features.evidence.models import (
    EvidenceIngestionBatch,
    EvidenceIngestionSnapshot,
)
from app.features.evidence.normalization import has_substance, normalize_markdown
from app.features.evidence.repository import EvidenceRepository

DOCUMENT_NAMESPACE = UUID("7afe3980-f2a6-47f8-8310-1d2f9faf12dc")


class EvidenceIngestionService:
    def __init__(
        self,
        repository: EvidenceRepository,
        chunker: MarkdownChunker,
        embedder: EvidenceEmbedder,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._chunker = chunker
        self._embedder = embedder
        self._now = now or (lambda: datetime.now(UTC))

    async def ingest(
        self,
        *,
        conversation_id: UUID,
        query_set: ResearchQuerySet,
        acquisition: ResearchAcquisitionSet,
        round_number: int = 0,
    ) -> EvidenceIngestionSnapshot:
        queries = (query_set.original_query, *query_set.diversified_queries)
        expected = [(query.query_id, query.text) for query in queries]
        actual = [
            (outcome.query_id, outcome.query_text) for outcome in acquisition.outcomes
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
            usable = [
                (
                    entry,
                    normalize_markdown(
                        entry[2].raw_content,
                        title=entry[2].title,
                    ),
                )
                for entry in entries
                if entry[2].raw_content is not None
            ]
            usable = [item for item in usable if has_substance(item[1])]
            if not usable:
                skipped.extend(
                    entry[2].result_id
                    for entry in entries
                    if entry[2].raw_content is not None
                )
                continue
            winner, content = min(
                usable,
                key=lambda item: (
                    -len(item[1]),
                    item[0][0],
                    item[0][2].rank,
                ),
            )
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            document_id = uuid5(
                DOCUMENT_NAMESPACE,
                f"{conversation_id}:{canonical_url}:{content_hash}",
            )
            document_chunks = self._chunker.chunk(document_id, content)
            if not document_chunks:
                skipped.extend(
                    entry[2].result_id
                    for entry in entries
                    if entry[2].raw_content is not None
                )
                continue
            document = EvidenceDocument(
                document_id=document_id,
                conversation_id=conversation_id,
                canonical_url=canonical_url,
                title=winner[2].title,
                content=content,
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
            chunks.extend(document_chunks)

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
            query_set=query_set,
            documents=tuple(documents),
            chunks=tuple(chunks),
            skipped_result_ids=tuple(skipped),
        )
        result = await self._repository.write_evidence(batch)
        if result.status == "missing":
            raise EvidenceIngestionError(
                "conversation_missing",
                "Conversation does not exist",
            )
        if result.status == "conflict":
            raise EvidenceIngestionError(
                "ingestion_conflict",
                "Acquisition identity was already used for different evidence",
            )
        if result.snapshot is None:
            raise RuntimeError("Evidence repository returned no snapshot")
        await self._embed(conversation_id)
        return result.snapshot

    async def _embed(self, conversation_id: UUID) -> None:
        while chunks := await self._repository.unembedded_chunks(
            conversation_id, self._embedder.version, self._embedder.batch_size
        ):
            vectors = await self._embedder.embed([text for _, text in chunks])
            written = await self._repository.write_embeddings(
                self._embedder.version,
                self._embedder.dimension,
                tuple(
                    (chunk_id, vector)
                    for (chunk_id, _), vector in zip(chunks, vectors, strict=True)
                ),
                self._now(),
            )
            if written is None:
                raise RuntimeError("Evidence changed while it was being embedded")
