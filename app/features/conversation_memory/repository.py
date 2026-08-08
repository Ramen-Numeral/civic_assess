from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.domain.conversation import (
    Conversation,
    ConversationStateSnapshot,
    StoredConversationTurn,
)


class AppendUserTurnStatus(StrEnum):
    CREATED = "created"
    EXISTING = "existing"
    CONFLICT = "conflict"
    MISSING = "missing"


@dataclass(frozen=True)
class AppendUserTurnResult:
    status: AppendUserTurnStatus
    turn: StoredConversationTurn | None = None


class StateWriteStatus(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    CONFLICT = "conflict"
    MISSING = "missing"
    INVALID_WATERMARK = "invalid_watermark"


@dataclass(frozen=True)
class StateWriteResult:
    status: StateWriteStatus
    state: ConversationStateSnapshot | None = None


class ConversationRepository(Protocol):
    async def create_conversation(
        self,
        conversation: Conversation,
    ) -> None: ...

    async def get_conversation(
        self,
        conversation_id: UUID,
    ) -> Conversation | None: ...

    async def get_turn(self, turn_id: UUID) -> StoredConversationTurn | None: ...

    async def get_conversation_state(
        self,
        conversation_id: UUID,
    ) -> ConversationStateSnapshot | None: ...

    async def write_conversation_state(
        self,
        snapshot: ConversationStateSnapshot,
        *,
        expected_revision: int | None,
    ) -> StateWriteResult: ...

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
        *,
        after_sequence: int | None = None,
        before_sequence: int | None = None,
        limit: int | None = None,
    ) -> tuple[StoredConversationTurn, ...]: ...

    async def delete_conversation(self, conversation_id: UUID) -> bool: ...
