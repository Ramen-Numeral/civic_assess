import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.conversation import Conversation, StoredConversationTurn
from app.features.conversation_context import ConversationContextService
from app.features.conversation_context.errors import ContextCatchUpRequiredError
from app.features.conversation_memory import ConversationService
from app.features.conversation_state import ConversationStateCoordinator
from app.features.input_validation.schemas import InputValidationRequest
from app.orchestration.orchestrator import ChatOrchestrator
from app.orchestration.state import ChatState


class ChatInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    client_message_id: UUID
    message: str


@dataclass(frozen=True)
class ChatInteractionResult:
    """Internal result; it is not a public transport contract."""

    user_turn: StoredConversationTurn
    workflow_state: ChatState


@dataclass
class _LockEntry:
    lock: asyncio.Lock
    users: int = 0


class _ConversationLocks:
    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._entries: dict[UUID, _LockEntry] = {}

    @asynccontextmanager
    async def hold(self, conversation_id: UUID) -> AsyncIterator[None]:
        async with self._guard:
            entry = self._entries.setdefault(
                conversation_id,
                _LockEntry(asyncio.Lock()),
            )
            entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            async with self._guard:
                entry.users -= 1
                if entry.users == 0:
                    del self._entries[conversation_id]


class ChatInteractionService:
    def __init__(
        self,
        conversations: ConversationService,
        contexts: ConversationContextService,
        orchestrator: ChatOrchestrator,
        *,
        state_coordinator: ConversationStateCoordinator | None = None,
    ) -> None:
        self._conversations = conversations
        self._contexts = contexts
        self._orchestrator = orchestrator
        self._state_coordinator = state_coordinator
        self._locks = _ConversationLocks()

    async def create_conversation(self) -> Conversation:
        return await self._conversations.create_conversation()

    async def interact(
        self,
        request: ChatInteractionRequest,
    ) -> ChatInteractionResult:
        async with self._locks.hold(request.conversation_id):
            user_turn = await self._conversations.append_user_turn(
                conversation_id=request.conversation_id,
                client_message_id=request.client_message_id,
                content=request.message,
            )
            try:
                context = await self._contexts.load(
                    conversation_id=request.conversation_id,
                    current_turn_id=user_turn.turn_id,
                )
            except ContextCatchUpRequiredError:
                if self._state_coordinator is None:
                    raise
                await self._state_coordinator.catch_up(
                    conversation_id=request.conversation_id,
                    before_sequence=user_turn.sequence_number,
                )
                context = await self._contexts.load(
                    conversation_id=request.conversation_id,
                    current_turn_id=user_turn.turn_id,
                )
            state = await self._orchestrator.invoke(
                InputValidationRequest(query=user_turn.content),
                conversation_context=context,
            )
            return ChatInteractionResult(
                user_turn=user_turn,
                workflow_state=state,
            )

    async def end_conversation(self, conversation_id: UUID) -> None:
        async with self._locks.hold(conversation_id):
            await self._conversations.end_conversation(conversation_id)
