from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.research import ResearchRequirement
from app.domain.validation import NonBlankText
from app.features.evidence_coverage.schemas import (
    EvidenceCoverageAssessment,
    EvidenceRef,
    RequirementRef,
)
from app.features.evidence_retrieval.schemas import EvidenceCandidate


class GroundedAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_query: NonBlankText
    requirements: tuple[ResearchRequirement, ...] = Field(min_length=1)
    coverage: EvidenceCoverageAssessment
    evidence: tuple[EvidenceCandidate, ...]

    @model_validator(mode="after")
    def require_consistent_grounding(self) -> "GroundedAnswerRequest":
        requirements = {item.requirement_id for item in self.requirements}
        represented = {
            item.requirement_id
            for item in (*self.coverage.findings, *self.coverage.gaps)
        }
        if len(requirements) != len(self.requirements) or not represented <= requirements:
            raise ValueError("Answer coverage references unknown requirements")
        evidence = {item.chunk_id for item in self.evidence}
        if len(evidence) != len(self.evidence):
            raise ValueError("Answer evidence chunk IDs must be unique")
        if any(
            chunk_id not in evidence
            for finding in self.coverage.findings
            for chunk_id in finding.supporting_chunk_ids
        ):
            raise ValueError("Answer coverage cites unavailable evidence")
        return self


class AnswerClaimProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_ref: RequirementRef
    text: NonBlankText
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    def model_post_init(self, __context: object) -> None:
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("Answer claim evidence references must be unique")


class GroundedAnswerProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: tuple[AnswerClaimProposal, ...] = Field(min_length=1, max_length=20)


class AtomicAnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: UUID
    requirement_id: UUID
    text: NonBlankText
    supporting_chunk_ids: tuple[UUID, ...] = Field(min_length=1)

    def model_post_init(self, __context: object) -> None:
        if len(set(self.supporting_chunk_ids)) != len(self.supporting_chunk_ids):
            raise ValueError("Answer claim support must be unique")


class GroundedAnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: tuple[AtomicAnswerClaim, ...] = Field(max_length=20)

    def model_post_init(self, __context: object) -> None:
        normalized = [" ".join(item.text.split()).casefold() for item in self.claims]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Atomic answer claims must be distinct")
