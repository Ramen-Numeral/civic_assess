from typing import Literal


QueryResolutionErrorCode = Literal["resolver_unavailable"]


class QueryResolutionError(ValueError):
    def __init__(
        self,
        code: QueryResolutionErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class InvalidQueryResolutionError(ValueError):
    """The resolver returned output that violates resolution invariants."""
