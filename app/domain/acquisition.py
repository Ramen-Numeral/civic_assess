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
