import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.observability.context import current_run_id, run_context
from app.roles import AgentRole

ProgressValue = str | int | float | bool | None


class ProgressStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class ProgressEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    sequence: int = Field(ge=1)
    phase: AgentRole
    status: ProgressStatus
    message: str
    details: dict[str, ProgressValue] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProgressReporter(Protocol):
    async def emit(self, event: ProgressEvent) -> None: ...


class NoOpProgressReporter:
    async def emit(self, event: ProgressEvent) -> None:
        pass


class ProgressEmitter:
    def __init__(self, reporter: ProgressReporter) -> None:
        self._reporter = reporter
        self._sequences: dict[str, int] = {}
        self._runs: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def run(self) -> AsyncIterator[str]:
        with run_context() as run_id:
            await self._begin_run(run_id)
            try:
                yield run_id
            finally:
                await self._end_run(run_id)

    async def _begin_run(self, run_id: str) -> None:
        async with self._lock:
            self._runs[run_id] = self._runs.get(run_id, 0) + 1
            self._sequences.setdefault(run_id, 0)

    async def _end_run(self, run_id: str) -> None:
        async with self._lock:
            remaining = self._runs[run_id] - 1
            if remaining:
                self._runs[run_id] = remaining
            else:
                del self._runs[run_id]
                del self._sequences[run_id]

    async def emit(
        self,
        phase: AgentRole,
        status: ProgressStatus,
        **details: ProgressValue,
    ) -> ProgressEvent:
        run_id = current_run_id()
        if run_id is None:
            raise RuntimeError("Progress emission requires a run context")
        async with self._lock:
            sequence = self._sequences.get(run_id, 0) + 1
            self._sequences[run_id] = sequence
            event = ProgressEvent(
                run_id=run_id,
                sequence=sequence,
                phase=phase,
                status=status,
                message=f"{phase.value.replace('_', ' ').title()} {status.value}.",
                details=details,
            )
            await self._reporter.emit(event)
        return event
