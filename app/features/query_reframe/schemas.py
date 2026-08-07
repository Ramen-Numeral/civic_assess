from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.domain.validation import InputGateAnalysis, NonBlankText


class QueryReframeMode(StrEnum):
    INTEGRITY_CLARIFICATION = "integrity_clarification"
    NEUTRAL = "neutral"
    SAFETY = "safety"


class QueryReframeProposal(BaseModel):
    """An untrusted model-written query that requires validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposed_query: NonBlankText


class QueryReframeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    normalized_query: NonBlankText
    analysis: InputGateAnalysis
    mode: QueryReframeMode
