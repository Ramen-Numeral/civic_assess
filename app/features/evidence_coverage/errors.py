from typing import Literal


EvidenceCoverageErrorCode = Literal["coverage_unavailable"]


class EvidenceCoverageError(ValueError):
    def __init__(
        self,
        code: EvidenceCoverageErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class InvalidEvidenceCoverageProposalError(ValueError):
    """Coverage output violated grounding or state invariants."""
