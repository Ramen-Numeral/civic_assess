from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.validation import NonBlankText


class OriginalResearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: UUID
    text: NonBlankText


class DiversifiedResearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: UUID
    requirement_ids: tuple[UUID, ...] = Field(min_length=1)
    evidence_angle: NonBlankText | None = None
    text: NonBlankText


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


class ResearchRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: UUID
    description: NonBlankText
    evidence_angles: tuple[NonBlankText, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_angles(self) -> "ResearchRequirement":
        if len(self.evidence_angles) != len(set(self.evidence_angles)):
            raise ValueError("Research evidence angles must be unique")
        return self


class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirements: tuple[ResearchRequirement, ...] = Field(min_length=1)
    query_set: ResearchQuerySet

    @model_validator(mode="after")
    def require_complete_angle_queries(self) -> "ResearchPlan":
        requirements = {item.requirement_id: item for item in self.requirements}
        if len(requirements) != len(self.requirements):
            raise ValueError("Research requirement IDs must be unique")
        queried: dict[UUID, list[str]] = {
            requirement_id: [] for requirement_id in requirements
        }
        for query in self.query_set.diversified_queries:
            if len(query.requirement_ids) != 1 or query.evidence_angle is None:
                raise ValueError("Initial queries must target exactly one angle")
            requirement_id = query.requirement_ids[0]
            if requirement_id not in requirements:
                raise ValueError("Initial query targets an unknown research angle")
            queried[requirement_id].append(query.evidence_angle)
        if any(
            tuple(queried[item.requirement_id]) != item.evidence_angles
            for item in self.requirements
        ):
            raise ValueError("Every research angle requires exactly one query")
        return self
