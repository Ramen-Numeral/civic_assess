import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.features.conversation_state.errors import (
    ConversationStateError,
    InvalidConversationStateProposalError,
)
from app.features.conversation_state.schemas import (
    ConversationStateProposal,
    ConversationSummaryRequest,
)
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.errors import FailureKind, LLMError
from app.prompts.base import Prompt


class ConversationStateService:
    def __init__(
        self,
        *,
        llm: LLMClient,
        prompt: Prompt,
        raw_turn_count: int,
    ) -> None:
        if not 0 <= raw_turn_count <= 50:
            raise ValueError("raw_turn_count must be between 0 and 50")
        self._llm = llm
        self._prompt = prompt
        self._raw_turn_count = raw_turn_count

    @property
    def raw_turn_count(self) -> int:
        return self._raw_turn_count

    async def summarize(
        self,
        request: ConversationSummaryRequest,
    ) -> ConversationStateProposal:
        turns = (
            request.recent_turns[-self._raw_turn_count:]
            if self._raw_turn_count
            else ()
        )
        if request.previous_state is None and not turns:
            raise InvalidConversationStateProposalError(
                "Summarization requires prior state or raw turns"
            )
        messages = [
            SystemMessage(content=self._prompt.build()),
            HumanMessage(content=json.dumps({
                "previous_state": (
                    request.previous_state.model_dump(mode="json")
                    if request.previous_state is not None
                    else None
                ),
                "recent_turn_order": "oldest_to_newest",
                "recent_turns": [
                    turn.model_dump(mode="json") for turn in turns
                ],
            }, ensure_ascii=False)),
        ]
        try:
            return await self._llm.invoke_structured(
                messages,
                ConversationStateProposal,
            )
        except LLMError as exc:
            if exc.failures and all(
                failure.kind is FailureKind.INVALID_OUTPUT
                for failure in exc.failures
            ):
                raise InvalidConversationStateProposalError(
                    "Summarizer returned invalid structured output"
                ) from exc
            raise ConversationStateError(
                "summarizer_unavailable",
                "I couldn't update conversation context right now.",
                retryable=True,
            ) from exc
