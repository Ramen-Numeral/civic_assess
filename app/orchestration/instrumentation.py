import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import TypeVar

from app.domain.validation import Disposition
from app.features.query_reframe.schemas import QueryReframeMode
from app.observability.progress import ProgressEmitter, ProgressStatus
from app.orchestration.state import ChatRoute
from app.roles import AgentRole


LOGGER = logging.getLogger(__name__)
StateT = TypeVar("StateT")
AgentNode = Callable[[StateT], Awaitable[dict[str, object]]]


def instrument_node(
    *,
    phase: AgentRole,
    node: AgentNode[StateT],
    emitter: ProgressEmitter,
) -> AgentNode[StateT]:
    async def observed(state: StateT) -> dict[str, object]:
        event = await emitter.emit(phase, ProgressStatus.STARTED)
        _log_phase(event.phase, event.status, event.sequence)
        started = perf_counter()
        try:
            update = await node(state)
        except Exception:
            duration_ms = round((perf_counter() - started) * 1000, 3)
            event = await emitter.emit(
                phase,
                ProgressStatus.FAILED,
                duration_ms=duration_ms,
            )
            _log_phase(event.phase, event.status, event.sequence, duration_ms)
            raise
        duration_ms = round((perf_counter() - started) * 1000, 3)
        event = await emitter.emit(
            phase,
            ProgressStatus.COMPLETED,
            duration_ms=duration_ms,
        )
        _log_phase(event.phase, event.status, event.sequence, duration_ms)
        return update

    return observed


def log_route(
    route: str | ChatRoute,
    reason: str,
    *,
    mode: QueryReframeMode | None = None,
    validation_disposition: Disposition | None = None,
) -> None:
    LOGGER.info(
        "Chat workflow routed",
        extra={
            "event": "chat.workflow.routed",
            "route": str(route),
            "reason": reason,
            "mode": mode.value if mode is not None else None,
            "validation_disposition": (
                validation_disposition.value
                if validation_disposition is not None
                else None
            ),
        },
    )


def _log_phase(
    phase: AgentRole,
    status: ProgressStatus,
    sequence: int,
    duration_ms: float | None = None,
) -> None:
    LOGGER.log(
        logging.WARNING if status is ProgressStatus.FAILED else logging.INFO,
        "Agent phase changed",
        extra={
            "event": "agent.phase.changed",
            "phase": phase.value,
            "status": status.value,
            "sequence": sequence,
            "duration_ms": duration_ms,
        },
    )
