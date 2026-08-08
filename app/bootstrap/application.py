from collections.abc import Mapping
from dataclasses import dataclass

from config.settings import Settings, load_application_config
from app.application import ChatInteractionService
from app.features.conversation_context import ConversationContextService
from app.features.conversation_memory import ConversationService
from app.features.conversation_state import (
    ConversationStateCoordinator,
    ConversationStateService,
)
from app.features.input_validation.service import InputValidationService
from app.features.query_diversification.service import QueryDiversificationService
from app.features.query_reframe.service import QueryReframeService
from app.features.query_resolution.service import QueryResolutionService
from app.features.research_acquisition.service import ResearchAcquisitionService
from app.infrastructure.llm.client import LLM
from app.infrastructure.llm.factory import build_llms
from app.infrastructure.persistence import (
    SQLiteConversationRepository,
    SQLiteDatabase,
)
from app.infrastructure.search import TavilySearchClient
from app.observability.logging import configure_logging
from app.orchestration.orchestrator import ChatOrchestrator
from app.prompts.factory import (
    build_conversation_state_prompt,
    build_input_validation_prompt,
    build_query_diversification_prompt,
    build_query_reframe_prompts,
    build_query_resolution_prompt,
)
from app.roles import AgentRole


@dataclass(frozen=True)
class Application:
    settings: Settings
    llms: Mapping[AgentRole, LLM]
    database: SQLiteDatabase
    conversations: ConversationService
    conversation_contexts: ConversationContextService
    chat_interactions: ChatInteractionService


def build_application(settings: Settings | None = None) -> Application:
    resolved = settings or load_application_config()
    configure_logging(debug=resolved.debug)
    database = SQLiteDatabase(resolved.sqlite_database_path)
    database.initialize()
    conversation_repository = SQLiteConversationRepository(database)
    conversations = ConversationService(
        conversation_repository,
        ttl_minutes=resolved.conversation_ttl_minutes,
    )
    conversation_contexts = ConversationContextService(
        conversation_repository,
        conversations,
        turn_retention=resolved.conversation_turn_retention,
    )
    llms = build_llms(resolved)
    conversation_state = ConversationStateService(
        llm=llms[AgentRole.CONVERSATION_SUMMARIZER],
        prompt=build_conversation_state_prompt(),
        raw_turn_count=resolved.conversation_summarizer_raw_turn_count,
    )
    state_coordinator = ConversationStateCoordinator(
        conversation_repository,
        conversations,
        conversation_state,
        turn_retention=resolved.conversation_turn_retention,
    )
    research_acquisition = (
        ResearchAcquisitionService(
            TavilySearchClient(
                api_key=resolved.tavily_api_key.get_secret_value(),
                timeout_seconds=resolved.tavily_timeout_seconds,
            ),
            results_per_query=resolved.research_results_per_query,
        )
        if resolved.tavily_api_key is not None
        else None
    )
    orchestrator = ChatOrchestrator(
        InputValidationService(
            llm=llms[AgentRole.VALIDATOR],
            prompt=build_input_validation_prompt(),
        ),
        QueryReframeService(
            llm=llms[AgentRole.REWRITER],
            prompts=build_query_reframe_prompts(),
        ),
        QueryResolutionService(
            llm=llms[AgentRole.QUERY_RESOLVER],
            prompt=build_query_resolution_prompt(),
        ),
        QueryDiversificationService(
            llm=llms[AgentRole.QUERY_DIVERSIFIER],
            prompt=build_query_diversification_prompt(),
            query_count=resolved.diversified_research_query_count,
        ),
        research_acquisition=research_acquisition,
    )
    chat_interactions = ChatInteractionService(
        conversations,
        conversation_contexts,
        orchestrator,
        state_coordinator=state_coordinator,
    )
    return Application(
        settings=resolved,
        llms=llms,
        database=database,
        conversations=conversations,
        conversation_contexts=conversation_contexts,
        chat_interactions=chat_interactions,
    )
