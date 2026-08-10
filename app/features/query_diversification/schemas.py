from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domain.research import QueryFacet
from app.domain.validation import NonBlankText
from app.features.evidence_coverage.schemas import EvidenceGap


DiversifiedQueryText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]
ResearchGoalText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]


class QueryDiversificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    validated_query: NonBlankText


class GapDirectedQueryPlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_query: NonBlankText
    gaps: tuple[EvidenceGap, ...] = Field(min_length=1, max_length=3)


class ProposedResearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    facet: QueryFacet
    text: DiversifiedQueryText
    research_goal: ResearchGoalText


class QueryDiversificationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    queries: tuple[ProposedResearchQuery, ...]
