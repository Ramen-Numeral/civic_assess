from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.domain.conversation import Conversation, StoredConversationTurn


class AppendUserTurnStatus(StrEnum):
    CREATED = "created"
    EXISTING = "existing"
    CONFLICT = "conflict"
    MISSING = "missing"


@dataclass(frozen=True)
class AppendUserTurnResult:
    status: AppendUserTurnStatus
    turn: StoredConversationTurn | None = None


class ConversationRepository(Protocol):
    async def create_conversation(
        self,
        conversation: Conversation,
    ) -> None: ...

    async def get_conversation(
        self,
        conversation_id: UUID,
    ) -> Conversation | None: ...

    async def append_user_turn(
        self,
        *,
        conversation_id: UUID,
        turn_id: UUID,
        client_message_id: UUID,
        content: str,
        created_at: datetime,
    ) -> AppendUserTurnResult: ...

    async def append_assistant_turn(
        self,
        *,
        conversation_id: UUID,
        turn_id: UUID,
        content: str,
        created_at: datetime,
    ) -> StoredConversationTurn | None: ...

    async def list_turns(
        self,
        conversation_id: UUID,
    ) -> tuple[StoredConversationTurn, ...]: ...

    async def delete_conversation(self, conversation_id: UUID) -> bool: ...
