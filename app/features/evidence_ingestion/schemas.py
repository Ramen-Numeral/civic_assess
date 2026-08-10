from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.domain.evidence import EvidenceChunk, EvidenceDocument
from app.domain.research import ResearchQuerySet


class EvidenceIngestionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    acquisition_id: UUID
    conversation_id: UUID
    round_number: int = Field(ge=0)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ingested_at: AwareDatetime
    query_set: ResearchQuerySet
    documents: tuple[EvidenceDocument, ...]
    chunks: tuple[EvidenceChunk, ...]
    skipped_result_ids: tuple[UUID, ...] = ()


class EvidenceIngestionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    acquisition_id: UUID
    conversation_id: UUID
    round_number: int = Field(ge=0)
    ingested_at: AwareDatetime
    document_ids: tuple[UUID, ...]
    chunk_ids: tuple[UUID, ...]
    skipped_result_ids: tuple[UUID, ...]
    new_document_count: int = Field(ge=0)
    reused_document_count: int = Field(ge=0)
    new_chunk_count: int = Field(ge=0)


EvidenceWriteStatus = Literal["created", "existing", "missing", "conflict"]


@dataclass(frozen=True)
class EvidenceWriteResult:
    status: EvidenceWriteStatus
    snapshot: EvidenceIngestionSnapshot | None = None
