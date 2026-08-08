from typing import Literal


QueryDiversificationErrorCode = Literal["diversifier_unavailable"]


class QueryDiversificationError(ValueError):
    def __init__(
        self,
        code: QueryDiversificationErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class InvalidQueryDiversificationError(ValueError):
    """The diversifier returned output that violates query invariants."""
