from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.domain.conversation import (
    ConversationStateSnapshot,
    StoredConversationTurn,
)

StateItem = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
StateSummary = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000),
]


class ConversationSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    previous_state: ConversationStateSnapshot | None = None
    recent_turns: tuple[StoredConversationTurn, ...] = ()

    @model_validator(mode="after")
    def require_consistent_turns(self) -> "ConversationSummaryRequest":
        sequences = [turn.sequence_number for turn in self.recent_turns]
        if sequences != sorted(set(sequences)):
            raise ValueError("Recent turns must be uniquely ordered by sequence")
        conversation_ids = {turn.conversation_id for turn in self.recent_turns}
        if len(conversation_ids) > 1:
            raise ValueError("Recent turns must belong to one conversation")
        if (
            self.previous_state is not None
            and conversation_ids
            and self.previous_state.conversation_id not in conversation_ids
        ):
            raise ValueError("State and turns belong to different conversations")
        return self


class ConversationStateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_goal: StateItem | None = None
    confirmed_decisions: tuple[StateItem, ...] = Field(default=(), max_length=20)
    rejected_proposals: tuple[StateItem, ...] = Field(default=(), max_length=20)
    superseded_decisions: tuple[StateItem, ...] = Field(default=(), max_length=20)
    active_constraints: tuple[StateItem, ...] = Field(default=(), max_length=20)
    open_questions: tuple[StateItem, ...] = Field(default=(), max_length=20)
    important_corrections: tuple[StateItem, ...] = Field(default=(), max_length=20)
    summary: StateSummary
