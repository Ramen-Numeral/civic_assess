from typing import Literal

EvidenceIngestionErrorCode = Literal[
    "invalid_acquisition",
    "no_extractable_evidence",
    "conversation_missing",
    "ingestion_conflict",
]


class EvidenceIngestionError(RuntimeError):
    def __init__(self, code: EvidenceIngestionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


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
