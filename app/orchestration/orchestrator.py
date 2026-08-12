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
from app.orchestration.answer import AnswerCoordinator
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
        answers: AnswerCoordinator | None = None,
    ) -> None:
        self._emitter = ProgressEmitter(reporter or NoOpProgressReporter())
        self._graph = build_chat_graph(
            input_validation,
            query_reframe,
            query_resolution,
            self._emitter,
            research=research,
            answers=answers,
        )

    async def invoke(
        self,
        request: InputValidationRequest,
        *,
        conversation_context: ConversationContext,
        approved_reframe: bool = False,
    ) -> ChatState:
        async with self._emitter.run():
            return await self._graph.ainvoke(
                {
                    "original_request": request.original_query,
                    "conversation_context": conversation_context,
                    "approved_reframe": approved_reframe,
                }
            )
