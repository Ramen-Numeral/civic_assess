from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.domain.validation import NonBlankText


CONVERSATION_CONTEXT_CHARACTER_LIMIT = 12_000


class ConversationRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ConversationContextStatus(StrEnum):
    RECENT_ONLY = "recent_only"
    COMPLETE = "complete"


class ContextSourceKind(StrEnum):
    RAW_TURN = "raw_turn"
    SUMMARY_STATE = "summary_state"


ContextExcerpt = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_id: UUID
    role: ConversationRole
    content: NonBlankText


class Conversation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    created_at: AwareDatetime
    expires_at: AwareDatetime


class StoredConversationTurn(ConversationTurn):
    conversation_id: UUID
    sequence_number: int = Field(gt=0)
    client_message_id: UUID | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def require_client_id_only_for_user_turns(self) -> "StoredConversationTurn":
        user_has_id = (
            self.role is ConversationRole.USER
            and self.client_message_id is not None
        )
        assistant_has_no_id = (
            self.role is ConversationRole.ASSISTANT
            and self.client_message_id is None
        )
        if not (user_has_id or assistant_has_no_id):
            raise ValueError("Only user turns require a client message ID")
        return self


class ConversationStateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_id: UUID
    conversation_id: UUID
    summary_through_sequence: int = Field(ge=0)
    revision: int = Field(ge=1)
    summarizer_version: NonBlankText
    current_goal: NonBlankText | None = None
    confirmed_decisions: tuple[NonBlankText, ...] = ()
    rejected_proposals: tuple[NonBlankText, ...] = ()
    superseded_decisions: tuple[NonBlankText, ...] = ()
    active_constraints: tuple[NonBlankText, ...] = ()
    open_questions: tuple[NonBlankText, ...] = ()
    important_corrections: tuple[NonBlankText, ...] = ()
    summary: NonBlankText
    updated_at: AwareDatetime


class ConversationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    current_turn_id: UUID
    recent_turns: tuple[ConversationTurn, ...] = ()
    state: ConversationStateSnapshot | None = None
    status: ConversationContextStatus

    @model_validator(mode="after")
    def require_consistent_sources(self) -> "ConversationContext":
        if any(turn.turn_id == self.current_turn_id for turn in self.recent_turns):
            raise ValueError("Current turn must remain separate from prior context")
        if len({turn.turn_id for turn in self.recent_turns}) != len(
            self.recent_turns
        ):
            raise ValueError("Conversation context turn IDs must be unique")
        if (
            self.state is not None
            and self.state.conversation_id != self.conversation_id
        ):
            raise ValueError("Conversation state belongs to another conversation")
        has_state = self.state is not None
        if (self.status is ConversationContextStatus.COMPLETE) != has_state:
            raise ValueError("Conversation context status must match state availability")
        return self


class ContextEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: ContextSourceKind
    source_id: UUID
    excerpt: ContextExcerpt
