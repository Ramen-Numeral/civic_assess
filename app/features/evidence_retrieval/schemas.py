from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.domain.validation import NonBlankText


class EvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: UUID
    document_id: UUID
    text: NonBlankText
    title: NonBlankText
    canonical_url: HttpUrl
    heading_path: tuple[NonBlankText, ...] = ()
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)


class ScoredEvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence: EvidenceCandidate
    rank: int = Field(ge=1)
    score: float


class QueryEvidenceRetrieval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: UUID
    query_text: NonBlankText
    lexical: tuple[ScoredEvidenceCandidate, ...]
    semantic: tuple[ScoredEvidenceCandidate, ...]


class EvidenceRetrievalSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    embedding_version: NonBlankText
    query_results: tuple[QueryEvidenceRetrieval, ...]
