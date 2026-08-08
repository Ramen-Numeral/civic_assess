from typing import Literal


ConversationErrorCode = Literal[
    "unknown_conversation",
    "expired_conversation",
    "client_message_conflict",
]


class ConversationError(ValueError):
    def __init__(self, code: ConversationErrorCode, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class UnknownConversationError(ConversationError):
    def __init__(self) -> None:
        super().__init__(
            "unknown_conversation",
            "That conversation does not exist.",
        )


class ExpiredConversationError(ConversationError):
    def __init__(self) -> None:
        super().__init__(
            "expired_conversation",
            "That conversation has expired.",
        )


class ClientMessageConflictError(ConversationError):
    def __init__(self) -> None:
        super().__init__(
            "client_message_conflict",
            "That message ID was already used for different content.",
        )
