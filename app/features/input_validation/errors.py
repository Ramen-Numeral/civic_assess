from typing import Literal


InputValidationErrorCode = Literal[
    "empty_query",
    "query_too_long",
    "validator_unavailable",
]


class InputValidationError(ValueError):
    def __init__(
        self,
        code: InputValidationErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
