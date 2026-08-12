from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.conversation import (
    CONVERSATION_CONTEXT_CHARACTER_LIMIT,
    ConversationStateSnapshot,
    StoredConversationTurn,
)
from app.features.conversation.repository import (
    ConversationRepository,
    StateWriteStatus,
)
from app.features.conversation.service import ConversationService
from app.features.conversation.errors import ConversationStateError
from app.features.conversation.schemas import ConversationSummaryRequest
from app.features.conversation.state import ConversationStateService


CONVERSATION_STATE_SUMMARIZER_VERSION = "v1"


class _StateWriteConflict(Exception):
    pass


class ConversationStateCoordinator:
    def __init__(
        self,
        repository: ConversationRepository,
        conversations: ConversationService,
        summarizer: ConversationStateService,
        *,
        turn_retention: int,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if turn_retention < 1:
            raise ValueError("turn_retention must be positive")
        self._repository = repository
        self._conversations = conversations
        self._summarizer = summarizer
        self._turn_retention = turn_retention
        self._now = now or (lambda: datetime.now(UTC))

    async def catch_up(
        self,
        *,
        conversation_id: UUID,
        before_sequence: int,
    ) -> ConversationStateSnapshot:
        for attempt in range(2):
            try:
                return await self._catch_up_once(
                    conversation_id=conversation_id,
                    before_sequence=before_sequence,
                )
            except _StateWriteConflict:
                if attempt == 1:
                    raise ConversationStateError(
                        "state_write_conflict",
                        "Conversation context changed while it was being updated.",
                        retryable=True,
                    ) from None
        raise AssertionError("unreachable")

    async def _catch_up_once(
        self,
        *,
        conversation_id: UUID,
        before_sequence: int,
    ) -> ConversationStateSnapshot:
        await self._conversations.get_conversation(conversation_id)
        state = await self._repository.get_conversation_state(conversation_id)
        watermark = state.summary_through_sequence if state is not None else 0
        if (
            before_sequence < 1
            or (state is not None and state.conversation_id != conversation_id)
            or watermark >= before_sequence
        ):
            raise self._invalid_history()

        turns = await self._repository.list_turns(
            conversation_id,
            after_sequence=watermark,
            before_sequence=before_sequence,
        )
        self._validate_turns(
            turns,
            conversation_id=conversation_id,
            after_sequence=watermark,
            before_sequence=before_sequence,
        )
        prefix = self._required_prefix(turns)
        if not prefix:
            if state is None:
                raise self._invalid_history()
            return state
        if self._summarizer.raw_turn_count == 0:
            raise ConversationStateError(
                "raw_turn_ingestion_disabled",
                "Conversation catch-up requires raw-turn summarization, but it "
                "is disabled by configuration.",
            )

        for start in range(0, len(prefix), self._summarizer.raw_turn_count):
            batch = prefix[
                start : start + self._summarizer.raw_turn_count
            ]
            proposal = await self._summarizer.summarize(
                ConversationSummaryRequest(
                    previous_state=state,
                    recent_turns=batch,
                )
            )
            snapshot = ConversationStateSnapshot(
                state_id=uuid4(),
                conversation_id=conversation_id,
                summary_through_sequence=batch[-1].sequence_number,
                revision=state.revision + 1 if state is not None else 1,
                summarizer_version=CONVERSATION_STATE_SUMMARIZER_VERSION,
                current_goal=proposal.current_goal,
                confirmed_decisions=proposal.confirmed_decisions,
                rejected_proposals=proposal.rejected_proposals,
                superseded_decisions=proposal.superseded_decisions,
                active_constraints=proposal.active_constraints,
                open_questions=proposal.open_questions,
                important_corrections=proposal.important_corrections,
                summary=proposal.summary,
                updated_at=self._now(),
            )
            expected_revision = state.revision if state is not None else None
            result = await self._repository.write_conversation_state(
                snapshot,
                expected_revision=expected_revision,
            )
            if result.status is StateWriteStatus.CONFLICT:
                raise _StateWriteConflict
            expected_status = (
                StateWriteStatus.UPDATED
                if state is not None
                else StateWriteStatus.CREATED
            )
            if result.status is not expected_status or result.state is None:
                raise self._invalid_history()
            state = result.state

        return state

    def _required_prefix(
        self,
        turns: tuple[StoredConversationTurn, ...],
    ) -> tuple[StoredConversationTurn, ...]:
        remaining_count = len(turns)
        remaining_characters = sum(len(turn.content) for turn in turns)
        prefix_length = 0
        while (
            remaining_count > self._turn_retention
            or remaining_characters > CONVERSATION_CONTEXT_CHARACTER_LIMIT
        ):
            turn = turns[prefix_length]
            prefix_length += 1
            remaining_count -= 1
            remaining_characters -= len(turn.content)
        return turns[:prefix_length]

    @staticmethod
    def _validate_turns(
        turns: tuple[StoredConversationTurn, ...],
        *,
        conversation_id: UUID,
        after_sequence: int,
        before_sequence: int,
    ) -> None:
        previous_sequence = after_sequence
        for turn in turns:
            if (
                turn.conversation_id != conversation_id
                or turn.sequence_number <= previous_sequence
                or turn.sequence_number >= before_sequence
            ):
                raise ConversationStateCoordinator._invalid_history()
            previous_sequence = turn.sequence_number

    @staticmethod
    def _invalid_history() -> ConversationStateError:
        return ConversationStateError(
            "invalid_conversation_history",
            "Conversation history is inconsistent and cannot be updated.",
        )
