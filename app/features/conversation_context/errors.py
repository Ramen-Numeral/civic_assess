from typing import Literal


ConversationContextErrorCode = Literal[
    "current_turn_unavailable",
    "context_catch_up_required",
    "invalid_conversation_state",
]


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
