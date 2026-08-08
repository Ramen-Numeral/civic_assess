from typing import Literal


ConversationStateErrorCode = Literal[
    "summarizer_unavailable",
    "raw_turn_ingestion_disabled",
    "invalid_conversation_history",
    "state_write_conflict",
]


class ConversationStateError(ValueError):
    def __init__(
        self,
        code: ConversationStateErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class InvalidConversationStateProposalError(ValueError):
    """Summarizer input or output violated the feature contract."""
