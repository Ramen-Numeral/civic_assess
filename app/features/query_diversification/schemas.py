from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.domain.research import OriginalResearchQuery, ResearchRequirement, TemporalScope
from app.domain.validation import NonBlankText
from app.features.evidence.models import EvidenceGap


PlanningText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]
class QueryDiversificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    validated_query: NonBlankText


class GapDirectedQueryPlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_query: OriginalResearchQuery
    requirements: tuple[ResearchRequirement, ...] = Field(min_length=1)
    gaps: tuple[EvidenceGap, ...] = Field(min_length=1, max_length=5)
    temporal_scope: TemporalScope = Field(default_factory=TemporalScope)

    @model_validator(mode="after")
    def require_known_gap_references(self) -> "GapDirectedQueryPlanningRequest":
        requirements = {item.requirement_id for item in self.requirements}
        for gap in self.gaps:
            if gap.requirement_id not in requirements:
                raise ValueError("Gap references an unknown requirement")
        return self


class ProposedEvidenceAngle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    description: PlanningText
    text: PlanningText


class ProposedResearchRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    description: PlanningText
    evidence_expectation: PlanningText | None = None
    evidence_angles: tuple[ProposedEvidenceAngle, ...] = Field(min_length=1)


class InitialResearchPlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    temporal_scope: TemporalScope
    requirements: tuple[ProposedResearchRequirement, ...] = Field(min_length=1)


class ProposedResearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    gap_refs: tuple[str, ...]


class GapQueryPlanningProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    queries: tuple[ProposedResearchQuery, ...]
