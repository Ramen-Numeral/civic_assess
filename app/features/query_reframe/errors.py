from typing import Literal


QueryReframeErrorCode = Literal[
    "reframer_unavailable",
]


class QueryReframeError(ValueError):
    def __init__(
        self,
        code: QueryReframeErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class InvalidReframeProposalError(ValueError):
    """The reframer could not produce a structurally valid proposal."""
