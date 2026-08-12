from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.features.evidence.models import (
    EvidenceIngestionBatch,
    EvidenceWriteResult,
)

if TYPE_CHECKING:
    from app.features.evidence.models import (
        EvidenceCandidate,
        ScoredEvidenceCandidate,
    )


class EvidenceRepository(Protocol):
    async def write_evidence(
        self,
        batch: EvidenceIngestionBatch,
    ) -> EvidenceWriteResult: ...

    async def unembedded_chunks(
        self, conversation_id: UUID, version: str, limit: int
    ) -> tuple[tuple[UUID, str], ...]: ...

    async def write_embeddings(
        self,
        version: str,
        dimension: int,
        rows: tuple[tuple[UUID, tuple[float, ...]], ...],
        created_at: datetime,
    ) -> int | None: ...

    async def search_evidence_text(
        self, conversation_id: UUID, query: str, limit: int
    ) -> tuple["ScoredEvidenceCandidate", ...]: ...

    async def load_evidence_vectors(
        self, conversation_id: UUID, version: str
    ) -> tuple[tuple["EvidenceCandidate", tuple[float, ...]], ...]: ...
