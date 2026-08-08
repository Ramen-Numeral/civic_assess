from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.conversation import Conversation, StoredConversationTurn
from app.features.conversation_memory.errors import (
    ClientMessageConflictError,
    ExpiredConversationError,
    UnknownConversationError,
)
from app.features.conversation_memory.repository import (
    AppendUserTurnStatus,
    ConversationRepository,
)


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository,
        *,
        ttl_minutes: int,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_minutes < 1:
            raise ValueError("ttl_minutes must be positive")
        self._repository = repository
        self._ttl = timedelta(minutes=ttl_minutes)
        self._now = now or (lambda: datetime.now(UTC))

    async def create_conversation(self) -> Conversation:
        created_at = self._now()
        conversation = Conversation(
            conversation_id=uuid4(),
            created_at=created_at,
            expires_at=created_at + self._ttl,
        )
        await self._repository.create_conversation(conversation)
        return conversation

    async def get_conversation(self, conversation_id: UUID) -> Conversation:
        return await self._require_active(conversation_id)

    async def append_user_turn(
        self,
        *,
        conversation_id: UUID,
        client_message_id: UUID,
        content: str,
    ) -> StoredConversationTurn:
        await self._require_active(conversation_id)
        result = await self._repository.append_user_turn(
            conversation_id=conversation_id,
            turn_id=uuid4(),
            client_message_id=client_message_id,
            content=content,
            created_at=self._now(),
        )
        if result.status is AppendUserTurnStatus.CONFLICT:
            raise ClientMessageConflictError()
        if result.status is AppendUserTurnStatus.MISSING or result.turn is None:
            raise UnknownConversationError()
        return result.turn

    async def append_assistant_turn(
        self,
        *,
        conversation_id: UUID,
        content: str,
    ) -> StoredConversationTurn:
        await self._require_active(conversation_id)
        turn = await self._repository.append_assistant_turn(
            conversation_id=conversation_id,
            turn_id=uuid4(),
            content=content,
            created_at=self._now(),
        )
        if turn is None:
            raise UnknownConversationError()
        return turn

    async def list_turns(
        self,
        conversation_id: UUID,
    ) -> tuple[StoredConversationTurn, ...]:
        await self._require_active(conversation_id)
        return await self._repository.list_turns(conversation_id)

    async def end_conversation(self, conversation_id: UUID) -> None:
        if not await self._repository.delete_conversation(conversation_id):
            raise UnknownConversationError()

    async def _require_active(self, conversation_id: UUID) -> Conversation:
        conversation = await self._repository.get_conversation(conversation_id)
        if conversation is None:
            raise UnknownConversationError()
        if conversation.expires_at <= self._now():
            await self._repository.delete_conversation(conversation_id)
            raise ExpiredConversationError()
        return conversation
