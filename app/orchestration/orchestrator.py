from app.domain.conversation import ConversationTurn
from app.features.input_validation.schemas import InputValidationRequest
from app.features.input_validation.service import InputValidationService
from app.features.query_diversification.service import QueryDiversificationService
from app.features.query_reframe.service import QueryReframeService
from app.features.query_resolution.service import QueryResolutionService
from app.observability.progress import (
    NoOpProgressReporter,
    ProgressEmitter,
    ProgressReporter,
)
from app.orchestration.graph import build_chat_graph
from app.orchestration.state import ChatState


class ChatOrchestrator:
    def __init__(
        self,
        input_validation: InputValidationService,
        query_reframe: QueryReframeService,
        query_resolution: QueryResolutionService,
        query_diversification: QueryDiversificationService,
        reporter: ProgressReporter | None = None,
    ) -> None:
        self._emitter = ProgressEmitter(reporter or NoOpProgressReporter())
        self._graph = build_chat_graph(
            input_validation,
            query_reframe,
            query_resolution,
            query_diversification,
            self._emitter,
        )

    async def invoke(
        self,
        request: InputValidationRequest,
        *,
        recent_turns: tuple[ConversationTurn, ...] = (),
    ) -> ChatState:
        async with self._emitter.run():
            return await self._graph.ainvoke(
                {
                    "original_request": request.query,
                    "recent_turns": recent_turns,
                }
            )
