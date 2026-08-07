from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from app.domain.conversation import ConversationTurn
from app.domain.validation import NonBlankText


EvidenceExcerpt = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
ClarificationQuestion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=240,
        pattern=r"^[^\r\n]+\?$",
    ),
]


class ResolutionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_id: UUID
    excerpt: EvidenceExcerpt


class QueryResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    normalized_query: NonBlankText
    recent_turns: tuple[ConversationTurn, ...] = ()


class QueryResolutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resolved_query: NonBlankText | None = None
    context_evidence: tuple[ResolutionEvidence, ...] = ()
    clarification_question: ClarificationQuestion | None = None

    @model_validator(mode="after")
    def require_one_outcome(self) -> "QueryResolutionResult":
        resolved = self.resolved_query is not None
        clarification = self.clarification_question is not None
        if resolved == clarification:
            raise ValueError(
                "Resolution requires either a query or clarification"
            )
        return self
