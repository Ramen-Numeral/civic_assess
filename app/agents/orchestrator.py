from app.agents.execution import GraphRunner
from app.agents.graph import build_input_validation_graph
from app.domain.validation import InputGateResult
from app.features.input_validation.schemas import InputValidationRequest
from app.features.input_validation.service import InputValidationService
from app.observability.progress import (
    NoOpProgressReporter,
    ProgressEmitter,
    ProgressReporter,
)


class InputValidationOrchestrator:
    def __init__(
        self,
        service: InputValidationService,
        reporter: ProgressReporter | None = None,
    ) -> None:
        emitter = ProgressEmitter(reporter or NoOpProgressReporter())
        self._graph = build_input_validation_graph(service, emitter)
        self._runner = GraphRunner(emitter)

    async def validate(self, request: InputValidationRequest) -> InputGateResult:
        state = await self._runner.invoke(
            self._graph,
            {"original_request": request.query},
        )
        return state["gate_result"]
