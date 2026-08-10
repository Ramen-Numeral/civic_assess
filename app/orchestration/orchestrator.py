from app.domain.conversation import ConversationContext
from app.features.input_validation.schemas import InputValidationRequest
from app.features.input_validation.service import InputValidationService
from app.features.query_reframe.service import QueryReframeService
from app.features.query_resolution.service import QueryResolutionService
from app.observability.progress import (
    NoOpProgressReporter,
    ProgressEmitter,
    ProgressReporter,
)
from app.orchestration.graph import build_chat_graph
from app.orchestration.research import ResearchCoordinator
from app.orchestration.state import ChatState


class ChatOrchestrator:
    def __init__(
        self,
        input_validation: InputValidationService,
        query_reframe: QueryReframeService,
        query_resolution: QueryResolutionService,
        reporter: ProgressReporter | None = None,
        *,
        research: ResearchCoordinator | None = None,
    ) -> None:
        self._emitter = ProgressEmitter(reporter or NoOpProgressReporter())
        self._graph = build_chat_graph(
            input_validation,
            query_reframe,
            query_resolution,
            self._emitter,
            research=research,
        )

    async def invoke(
        self,
        request: InputValidationRequest,
        *,
        conversation_context: ConversationContext,
    ) -> ChatState:
        async with self._emitter.run():
            return await self._graph.ainvoke(
                {
                    "original_request": request.query,
                    "conversation_context": conversation_context,
                }
            )
