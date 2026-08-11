from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domain.validation import NonBlankText
from app.domain.research import TemporalScope
from app.features.evidence_coverage.schemas import EvidenceBasis, SourceFitness
from app.features.evidence_retrieval.schemas import EvidenceCandidate


FindingRef = Annotated[str, StringConstraints(pattern=r"^F[1-9]\d*$")]
ParagraphRef = Annotated[str, StringConstraints(pattern=r"^P[1-9]\d*$")]


class AnswerFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: NonBlankText
    supporting_chunk_ids: tuple[UUID, ...] = Field(min_length=1)
    evidence_basis: EvidenceBasis
    source_fitness: SourceFitness
    qualification: NonBlankText | None = None


class GroundedAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_query: NonBlankText
    temporal_scope: TemporalScope = Field(default_factory=TemporalScope)
    findings: tuple[AnswerFinding, ...]
    evidence: tuple[EvidenceCandidate, ...]
    unresolved: tuple[NonBlankText, ...] = ()

    @model_validator(mode="after")
    def require_consistent_grounding(self) -> "GroundedAnswerRequest":
        available = {item.chunk_id for item in self.evidence}
        if len(available) != len(self.evidence) or any(
            chunk not in available
            for finding in self.findings for chunk in finding.supporting_chunk_ids
        ):
            raise ValueError("Answer findings require unique available evidence")
        return self


class AnswerParagraphProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: NonBlankText
    finding_refs: tuple[FindingRef, ...] = Field(min_length=1)


class GroundedAnswerProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    paragraphs: tuple[AnswerParagraphProposal, ...] = Field(min_length=1, max_length=6)


class ParagraphSupportProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    paragraph_ref: ParagraphRef
    support: int = Field(ge=1, le=5)
    scope: int = Field(ge=1, le=5)


class AnswerAuditProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    paragraph_support: tuple[ParagraphSupportProposal, ...] = Field(min_length=1)
    answer_quality: int = Field(ge=1, le=5)
    revision_instructions: tuple[NonBlankText, ...] = Field(default=(), max_length=5)
    evidence_note: NonBlankText


class AnswerParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    paragraph_id: UUID
    text: NonBlankText
    finding_indexes: tuple[int, ...] = Field(min_length=1)
    supporting_chunk_ids: tuple[UUID, ...] = Field(min_length=1)


class NaturalAnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    paragraphs: tuple[AnswerParagraph, ...] = Field(max_length=6)


class AnswerAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    paragraph_support: dict[UUID, Annotated[int, Field(ge=1, le=5)]]
    answer_quality: int = Field(ge=1, le=5)
    revision_instructions: tuple[NonBlankText, ...] = ()
    evidence_note: NonBlankText

    def passes(self, minimum: int) -> bool:
        return self.answer_quality >= minimum and all(
            rating >= minimum for rating in self.paragraph_support.values()
        )

    @property
    def verdict(self) -> str:
        return "pass" if self.passes(5) else "revise"

    @property
    def unsupported_paragraph_ids(self) -> tuple[UUID, ...]:
        return tuple(
            paragraph_id
            for paragraph_id, rating in self.paragraph_support.items()
            if rating <= 2
        )
