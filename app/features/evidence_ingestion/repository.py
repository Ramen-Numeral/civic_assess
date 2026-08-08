from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.domain.evidence import EvidenceIngestionBatch, EvidenceIngestionSnapshot


class EvidenceWriteStatus(StrEnum):
    CREATED = "created"
    EXISTING = "existing"
    MISSING = "missing"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class EvidenceWriteResult:
    status: EvidenceWriteStatus
    snapshot: EvidenceIngestionSnapshot | None = None


class EvidenceRepository(Protocol):
    async def write_evidence(
        self,
        batch: EvidenceIngestionBatch,
    ) -> EvidenceWriteResult: ...
