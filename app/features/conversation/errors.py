from typing import Literal


ConversationErrorCode = Literal[
    "unknown_conversation",
    "expired_conversation",
    "client_message_conflict",
]
ConversationContextErrorCode = Literal[
    "current_turn_unavailable",
    "context_catch_up_required",
    "invalid_conversation_state",
]
ConversationStateErrorCode = Literal[
    "summarizer_unavailable",
    "raw_turn_ingestion_disabled",
    "invalid_conversation_history",
    "state_write_conflict",
]


class ConversationError(ValueError):
    def __init__(self, code: ConversationErrorCode, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class UnknownConversationError(ConversationError):
    def __init__(self) -> None:
        super().__init__("unknown_conversation", "That conversation does not exist.")


class ExpiredConversationError(ConversationError):
    def __init__(self) -> None:
        super().__init__("expired_conversation", "That conversation has expired.")


class ClientMessageConflictError(ConversationError):
    def __init__(self) -> None:
        super().__init__(
            "client_message_conflict",
            "That message ID was already used for different content.",
        )


class ConversationContextError(ValueError):
    def __init__(self, code: ConversationContextErrorCode, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class CurrentTurnUnavailableError(ConversationContextError):
    def __init__(self) -> None:
        super().__init__(
            "current_turn_unavailable",
            "The current conversation turn is unavailable.",
        )


class ContextCatchUpRequiredError(ConversationContextError):
    def __init__(self) -> None:
        super().__init__(
            "context_catch_up_required",
            "Conversation context must be updated before continuing.",
        )


class InvalidConversationStateError(ConversationContextError):
    def __init__(self) -> None:
        super().__init__(
            "invalid_conversation_state",
            "Conversation context is inconsistent.",
        )


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
