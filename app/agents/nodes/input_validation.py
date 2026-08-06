from collections.abc import Awaitable, Callable

from app.agents.state import ConversationState
from app.features.input_validation.schemas import InputValidationRequest
from app.features.input_validation.service import InputValidationService


InputValidationNode = Callable[
    [ConversationState],
    Awaitable[dict[str, object]],
]


def build_input_validation_node(
    service: InputValidationService,
) -> InputValidationNode:
    async def validate_input(state: ConversationState) -> dict[str, object]:
        result = await service.validate(
            InputValidationRequest(
                query=state["original_request"],
            )
        )
        return {
            "normalized_query": result.normalized_query,
            "gate_result": result,
        }

    return validate_input
