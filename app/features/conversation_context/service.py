from uuid import UUID

from app.domain.conversation import (
    CONVERSATION_CONTEXT_CHARACTER_LIMIT,
    ConversationContext,
    ConversationContextStatus,
    ConversationRole,
    StoredConversationTurn,
)
from app.features.conversation_context.errors import (
    ContextCatchUpRequiredError,
    CurrentTurnUnavailableError,
    InvalidConversationStateError,
)
from app.features.conversation_memory.repository import ConversationRepository
from app.features.conversation_memory.service import ConversationService


class ConversationContextService:
    def __init__(
        self,
        repository: ConversationRepository,
        conversations: ConversationService,
        *,
        turn_retention: int,
    ) -> None:
        if turn_retention < 1:
            raise ValueError("turn_retention must be positive")
        self._repository = repository
        self._conversations = conversations
        self._turn_retention = turn_retention

    async def load(
        self,
        *,
        conversation_id: UUID,
        current_turn_id: UUID,
    ) -> ConversationContext:
        await self._conversations.get_conversation(conversation_id)
        current_turn = await self._repository.get_turn(current_turn_id)
        if (
            current_turn is None
            or current_turn.conversation_id != conversation_id
            or current_turn.role is not ConversationRole.USER
        ):
            raise CurrentTurnUnavailableError()

        state = await self._repository.get_conversation_state(conversation_id)
        if state is None:
            turns = await self._repository.list_turns(
                conversation_id,
                before_sequence=current_turn.sequence_number,
                limit=self._turn_retention + 1,
            )
            if self._requires_catch_up(turns):
                raise ContextCatchUpRequiredError()
            return ConversationContext(
                conversation_id=conversation_id,
                current_turn_id=current_turn_id,
                recent_turns=turns,
                status=ConversationContextStatus.RECENT_ONLY,
            )

        if state.summary_through_sequence >= current_turn.sequence_number:
            raise InvalidConversationStateError()
        turns = await self._repository.list_turns(
            conversation_id,
            after_sequence=state.summary_through_sequence,
            before_sequence=current_turn.sequence_number,
        )
        if self._requires_catch_up(turns):
            raise ContextCatchUpRequiredError()
        return ConversationContext(
            conversation_id=conversation_id,
            current_turn_id=current_turn_id,
            recent_turns=turns,
            state=state,
            status=ConversationContextStatus.COMPLETE,
        )

    def _requires_catch_up(
        self,
        turns: tuple[StoredConversationTurn, ...],
    ) -> bool:
        return (
            len(turns) > self._turn_retention
            or sum(len(turn.content) for turn in turns)
            > CONVERSATION_CONTEXT_CHARACTER_LIMIT
        )
