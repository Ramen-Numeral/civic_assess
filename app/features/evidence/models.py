from dataclasses import dataclass
from collections.abc import Iterable
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    computed_field,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
)

from app.domain.evidence import EvidenceChunk, EvidenceDocument
from app.domain.research import ResearchQuerySet, ResearchRequirement, TemporalScope
from app.domain.validation import NonBlankText


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


RequirementRef = Annotated[str, StringConstraints(pattern=r"^R[1-9]\d*$")]
EvidenceRef = Annotated[str, StringConstraints(pattern=r"^E[1-9]\d*$")]
EvidenceBasis = Literal["observed", "projected", "not_applicable"]
SourceFitness = Literal["fit", "qualified"]


class EvidenceCoverageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_query: NonBlankText
    temporal_scope: TemporalScope = Field(default_factory=TemporalScope)
    requirements: tuple[ResearchRequirement, ...] = Field(min_length=1)
    evidence_view: tuple[EvidenceCandidate, ...] = ()


class RequirementFindingProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_ref: RequirementRef
    statement: NonBlankText
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    evidence_basis: EvidenceBasis
    source_fitness: SourceFitness
    qualification: NonBlankText | None = None


class EvidenceGapProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_ref: RequirementRef
    description: NonBlankText
    missing_evidence: NonBlankText


class EvidenceCoverageProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    findings: tuple[RequirementFindingProposal, ...] = ()
    gaps: tuple[EvidenceGapProposal, ...] = Field(default=(), max_length=5)


class RequirementFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: UUID
    statement: NonBlankText
    supporting_chunk_ids: tuple[UUID, ...] = Field(min_length=1)
    evidence_basis: EvidenceBasis
    source_fitness: SourceFitness
    qualification: NonBlankText | None = None

    def model_post_init(self, __context: object) -> None:
        if len(set(self.supporting_chunk_ids)) != len(self.supporting_chunk_ids):
            raise ValueError("Finding support must be unique")
        if (
            self.evidence_basis == "projected"
            or self.source_fitness == "qualified"
        ) and self.qualification is None:
            raise ValueError("Projected or qualified findings need a qualification")


class EvidenceGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: UUID
    description: NonBlankText
    missing_evidence: NonBlankText


class EvidenceCoverageAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    findings: tuple[RequirementFinding, ...] = ()
    gaps: tuple[EvidenceGap, ...] = Field(default=(), max_length=5)

    def model_post_init(self, __context: object) -> None:
        if not self.findings and not self.gaps:
            raise ValueError("Coverage requires a finding or gap")
        if _duplicates(
            (item.requirement_id, item.statement) for item in self.findings
        ):
            raise ValueError("Requirement findings must be distinct")
        if _duplicates(
            (item.requirement_id, item.description) for item in self.gaps
        ):
            raise ValueError("Evidence gaps must be distinct")

    @computed_field
    @property
    def sufficient(self) -> bool:
        return bool(self.findings) and not self.gaps


def _duplicates(values: Iterable[tuple[UUID, str]]) -> bool:
    normalized = [
        (owner, " ".join(value.split()).casefold()) for owner, value in values
    ]
    return len(normalized) != len(set(normalized))
