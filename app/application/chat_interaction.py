import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.application.workflow_response import (
    pending_reframe_query,
    workflow_response,
)
from app.domain.conversation import (
    Conversation,
    ConversationContext,
    ConversationRole,
)
from app.features.conversation import (
    ConversationContextService,
    ConversationService,
    ConversationStateCoordinator,
)
from app.features.conversation.errors import ContextCatchUpRequiredError
from app.features.input_validation.schemas import InputValidationRequest
from app.orchestration.orchestrator import ChatOrchestrator
from app.orchestration.state import ChatRoute, ChatState


class ChatInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    client_message_id: UUID
    message: str


@dataclass(frozen=True)
class ChatInteractionResult:
    """Internal result; it is not a public transport contract."""

    response_text: str | None
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
                pending_reframe = _pending_reframe(context)
                decision = user_turn.content.strip().casefold()
                if pending_reframe is not None and decision == "yes":
                    if not await self._conversations.discard_latest_user_turn(
                        request.conversation_id,
                        user_turn.turn_id,
                    ):
                        raise RuntimeError("Reframe approval could not be promoted")
                    user_turn = await self._conversations.append_user_turn(
                        conversation_id=request.conversation_id,
                        client_message_id=request.client_message_id,
                        content=pending_reframe,
                    )
                    context = context.model_copy(
                        update={
                            "current_turn_id": user_turn.turn_id,
                        }
                    )
                    state = await self._orchestrator.invoke(
                        InputValidationRequest(original_query=pending_reframe),
                        conversation_context=context,
                        approved_reframe=True,
                    )
                elif pending_reframe is not None and decision == "no":
                    state = ChatState(
                        original_request=user_turn.content,
                        conversation_context=context,
                        chat_route=ChatRoute.REFRAME_DECLINED,
                    )
                else:
                    state = await self._orchestrator.invoke(
                        InputValidationRequest(original_query=user_turn.content),
                        conversation_context=context,
                    )
            except Exception:
                await self._conversations.discard_latest_user_turn(
                    request.conversation_id,
                    user_turn.turn_id,
                )
                raise
            response = workflow_response(state)
            ephemeral = (
                response is not None
                and "answer_result" not in state
                and state.get("chat_route")
                not in {
                    ChatRoute.AWAIT_APPROVAL,
                    ChatRoute.AWAIT_CLARIFICATION,
                    ChatRoute.REFRAME_DECLINED,
                }
            )
            if ephemeral and not await self._conversations.discard_latest_user_turn(
                request.conversation_id,
                user_turn.turn_id,
            ):
                raise RuntimeError("Provisional user turn could not be discarded")
            if response is not None and not ephemeral:
                await self._conversations.append_assistant_turn(
                    conversation_id=request.conversation_id,
                    content=response,
                )
            return ChatInteractionResult(
                response_text=response,
                workflow_state=state,
            )


def _pending_reframe(context: ConversationContext) -> str | None:
    if not context.recent_turns:
        return None
    latest = context.recent_turns[-1]
    if latest.role is not ConversationRole.ASSISTANT:
        return None
    return pending_reframe_query(latest.content)
