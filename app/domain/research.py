from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)

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
    evidence_expectation: NonBlankText | None = None
    evidence_angles: tuple[NonBlankText, ...] = ()

    @model_validator(mode="after")
    def require_unique_angles(self) -> "ResearchRequirement":
        if len(self.evidence_angles) != len(set(self.evidence_angles)):
            raise ValueError("Research evidence angles must be unique")
        return self


class TemporalScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    spans: tuple[NonBlankText, ...] = ()


class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirements: tuple[ResearchRequirement, ...] = Field(min_length=1)
    query_set: ResearchQuerySet
    temporal_scope: TemporalScope = Field(default_factory=TemporalScope)

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


class AcquisitionFailureCode(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    UNAUTHORIZED = "unauthorized"
    REQUEST_REJECTED = "request_rejected"
    INVALID_RESPONSE = "invalid_response"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class AcquiredSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result_id: UUID
    rank: int = Field(ge=1, le=5)
    title: NonBlankText
    url: HttpUrl
    snippet: NonBlankText
    raw_content: NonBlankText | None = None
    provider_score: float | None = Field(default=None, ge=0)
    provider_result_id: NonBlankText | None = None


class QueryAcquisitionSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["success"] = "success"
    query_id: UUID
    query_text: NonBlankText
    provider_request_id: NonBlankText
    credits_used: int | None = Field(default=None, ge=0)
    results: tuple[AcquiredSearchResult, ...] = Field(max_length=5)


class QueryAcquisitionFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["failure"] = "failure"
    query_id: UUID
    query_text: NonBlankText
    error_code: AcquisitionFailureCode
    retryable: bool


QueryAcquisitionOutcome = Annotated[
    QueryAcquisitionSuccess | QueryAcquisitionFailure,
    Field(discriminator="status"),
]


class ResearchAcquisitionSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    acquisition_id: UUID
    acquired_at: AwareDatetime
    outcomes: tuple[QueryAcquisitionOutcome, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_queries(self) -> "ResearchAcquisitionSet":
        query_ids = [outcome.query_id for outcome in self.outcomes]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("Acquisition query IDs must be unique")
        return self
