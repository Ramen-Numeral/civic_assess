from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.validation import NonBlankText


class QueryFacet(StrEnum):
    PRIMARY_RECORD = "primary_record"
    ACTOR_POSITION = "actor_position"
    FACTUAL_CONTEXT = "factual_context"
    CONSEQUENCE_OR_EFFECT = "consequence_or_effect"
    COUNTERARGUMENT_OR_CRITIQUE = "counterargument_or_critique"
    DISPUTE_OR_UNCERTAINTY = "dispute_or_uncertainty"


class OriginalResearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: UUID
    text: NonBlankText


class DiversifiedResearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: UUID
    facet: QueryFacet
    text: NonBlankText
    research_goal: NonBlankText


class ResearchQuerySet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_query: OriginalResearchQuery
    diversified_queries: tuple[DiversifiedResearchQuery, ...]

    @model_validator(mode="after")
    def require_unique_query_ids(self) -> "ResearchQuerySet":
        query_ids = {
            self.original_query.query_id,
            *(query.query_id for query in self.diversified_queries),
        }
        if len(query_ids) != len(self.diversified_queries) + 1:
            raise ValueError("Research query IDs must be unique")
        return self
