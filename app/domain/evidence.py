from enum import StrEnum
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)

from app.domain.research import QueryFacet
from app.domain.validation import NonBlankText


class EvidenceQueryKind(StrEnum):
    ORIGINAL = "original"
    DIVERSIFIED = "diversified"


class EvidenceResearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: UUID
    position: int = Field(ge=0)
    kind: EvidenceQueryKind
    text: NonBlankText
    facet: QueryFacet | None = None
    research_goal: NonBlankText | None = None


class EvidenceDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: UUID
    result_id: UUID
    rank: int = Field(ge=1, le=5)
    original_url: HttpUrl
    provider_result_id: NonBlankText | None = None


class EvidenceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID
    conversation_id: UUID
    canonical_url: HttpUrl
    title: NonBlankText
    raw_content: NonBlankText
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    acquired_at: AwareDatetime
    discoveries: tuple[EvidenceDiscovery, ...] = Field(min_length=1)


class EvidenceChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: UUID
    document_id: UUID
    chunk_index: int = Field(ge=0)
    text: NonBlankText
    heading_path: tuple[NonBlankText, ...] = ()
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunker_version: NonBlankText

    @model_validator(mode="after")
    def require_valid_offsets(self) -> "EvidenceChunk":
        if self.end_offset <= self.start_offset:
            raise ValueError("Chunk end offset must follow its start offset")
        return self


class EvidenceIngestionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    acquisition_id: UUID
    conversation_id: UUID
    round_number: int = Field(ge=0)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ingested_at: AwareDatetime
    queries: tuple[EvidenceResearchQuery, ...] = Field(min_length=1)
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
