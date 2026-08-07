from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.validation import NonBlankText


class ConversationRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_id: UUID
    role: ConversationRole
    content: NonBlankText
