from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.domain.validation import NonBlankText


class ConversationRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


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
