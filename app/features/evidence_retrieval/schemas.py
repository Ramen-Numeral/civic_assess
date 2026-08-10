from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl

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
    last_discovered_at: AwareDatetime


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


class RankedEvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence: EvidenceCandidate
    rank: int = Field(ge=1)
    rrf_score: float = Field(gt=0)
    supporting_query_ids: tuple[UUID, ...]


class EvidenceRetrievalSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    embedding_version: NonBlankText | None
    query_results: tuple[QueryEvidenceRetrieval, ...]
    ranked_candidates: tuple[RankedEvidenceCandidate, ...]
