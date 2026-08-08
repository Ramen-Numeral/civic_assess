from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from app.domain.conversation import ConversationContext, ContextEvidenceReference
from app.domain.validation import NonBlankText


ClarificationQuestion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=240,
        pattern=r"^[^\r\n]+\?$",
    ),
]


class QueryResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    normalized_query: NonBlankText
    context: ConversationContext


class QueryResolutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resolved_query: NonBlankText | None = None
    context_evidence: tuple[ContextEvidenceReference, ...] = ()
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
