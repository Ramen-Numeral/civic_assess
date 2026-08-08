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
