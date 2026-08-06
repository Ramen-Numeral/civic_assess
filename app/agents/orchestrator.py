from app.agents.graph import build_input_validation_graph
from app.domain.validation import InputGateResult
from app.features.input_validation.schemas import InputValidationRequest
from app.features.input_validation.service import InputValidationService


class InputValidationOrchestrator:
    def __init__(self, service: InputValidationService) -> None:
        self._graph = build_input_validation_graph(service)

    async def validate(self, request: InputValidationRequest) -> InputGateResult:
        state = await self._graph.ainvoke(
            {"original_request": request.query}
        )
        return state["gate_result"]
