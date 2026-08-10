from collections.abc import Iterable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.validation import NonBlankText
from app.features.evidence_retrieval.schemas import EvidenceRetrievalSet


class EvidenceCoverageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_query: NonBlankText
    retrieval: EvidenceRetrievalSet


class CoveredEvidencePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: NonBlankText
    supporting_chunk_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_support(self) -> "CoveredEvidencePoint":
        if len(set(self.supporting_chunk_ids)) != len(self.supporting_chunk_ids):
            raise ValueError("Covered point support must be unique")
        return self


class EvidenceGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    description: NonBlankText
    research_goal: NonBlankText


class EvidenceCoverageAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sufficient: bool
    covered_points: tuple[CoveredEvidencePoint, ...] = ()
    gaps: tuple[EvidenceGap, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def require_consistent_assessment(self) -> "EvidenceCoverageAssessment":
        if self.sufficient and (not self.covered_points or self.gaps):
            raise ValueError("Sufficient coverage requires grounded points and no gaps")
        if not self.sufficient and not self.gaps:
            raise ValueError("Insufficient coverage requires at least one gap")
        if _duplicates(point.statement for point in self.covered_points):
            raise ValueError("Covered points must be distinct")
        if _duplicates(gap.description for gap in self.gaps):
            raise ValueError("Gap descriptions must be distinct")
        if _duplicates(gap.research_goal for gap in self.gaps):
            raise ValueError("Gap research goals must be distinct")
        return self


def _duplicates(values: Iterable[str]) -> bool:
    normalized = [" ".join(value.split()).casefold() for value in values]
    return len(normalized) != len(set(normalized))
