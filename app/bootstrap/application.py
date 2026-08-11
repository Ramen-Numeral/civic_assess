from collections.abc import Mapping
from dataclasses import dataclass

from config.settings import Settings, load_application_config
from app.application import ChatInteractionService
from app.features.answer_synthesis import AnswerSynthesisService
from app.features.claim_verification import ClaimVerificationService
from app.features.conversation_context import ConversationContextService
from app.features.conversation_memory import ConversationService
from app.features.conversation_state import (
    ConversationStateCoordinator,
    ConversationStateService,
)
from app.features.input_validation.service import InputValidationService
from app.features.evidence_ingestion.chunker import MarkdownChunker
from app.features.evidence_ingestion.embedder import EvidenceEmbedder
from app.features.evidence_ingestion.service import EvidenceIngestionService
from app.features.evidence_coverage import EvidenceCoverageService
from app.features.evidence_retrieval.service import EvidenceRetrievalService
from app.features.query_diversification.service import QueryDiversificationService
from app.features.query_reframe.service import QueryReframeService
from app.features.query_resolution.service import QueryResolutionService
from app.features.research_acquisition.service import ResearchAcquisitionService
from app.infrastructure.llm.client import LLM
from app.infrastructure.llm.factory import build_llms
from app.infrastructure.persistence import (
    SQLiteConversationRepository,
    SQLiteDatabase,
    SQLiteEvidenceRepository,
)
from app.infrastructure.search import TavilySearchClient
from app.observability.logging import configure_logging
from app.observability.llm_usage import LLMUsageReporter
from app.orchestration.orchestrator import ChatOrchestrator
from app.orchestration.answer import AnswerCoordinator
from app.orchestration.research import ResearchCoordinator
from app.prompts.factory import (
    build_conversation_state_prompt,
    build_answer_repair_prompt,
    build_answer_composition_prompt,
    build_answer_synthesis_prompt,
    build_evidence_coverage_prompt,
    build_gap_query_planning_prompt,
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
    answer_synthesis: AnswerSynthesisService
    claim_verification: ClaimVerificationService
    answers: AnswerCoordinator
    evidence_coverage: EvidenceCoverageService
    evidence_retrieval: EvidenceRetrievalService
    research: ResearchCoordinator
    chat_interactions: ChatInteractionService


def build_application(
    settings: Settings | None = None,
    *,
    llm_usage_reporter: LLMUsageReporter | None = None,
) -> Application:
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
    llms = (
        build_llms(resolved, llm_usage_reporter)
        if llm_usage_reporter is not None
        else build_llms(resolved)
    )
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
    evidence_coverage = EvidenceCoverageService(
        llm=llms[AgentRole.EVIDENCE_COVERAGE],
        prompt=build_evidence_coverage_prompt(),
    )
    answer_synthesis = AnswerSynthesisService(
        llm=llms[AgentRole.ANSWER_WRITER],
        prompt=build_answer_synthesis_prompt(),
        repair_prompt=build_answer_repair_prompt(),
        composition_prompt=build_answer_composition_prompt(),
    )
    claim_verification = ClaimVerificationService(
        resolved.claim_nli_model_id,
        resolved.claim_nli_model_revision,
        batch_size=resolved.claim_nli_batch_size,
        device=resolved.claim_nli_device,
        entailment_threshold=resolved.claim_nli_entailment_threshold,
        contradiction_threshold=resolved.claim_nli_contradiction_threshold,
    )
    answers = AnswerCoordinator(answer_synthesis, claim_verification)
    query_planner = QueryDiversificationService(
        llm=llms[AgentRole.QUERY_DIVERSIFIER],
        prompt=build_query_diversification_prompt(),
        gap_prompt=build_gap_query_planning_prompt(),
        query_count=resolved.diversified_research_query_count,
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
    evidence_repository = SQLiteEvidenceRepository(database)
    evidence_embedder = EvidenceEmbedder(
        resolved.evidence_embedding_model_id,
        resolved.evidence_embedding_model_revision,
        batch_size=resolved.evidence_embedding_batch_size,
        device=resolved.evidence_embedding_device,
    )
    evidence_retrieval = EvidenceRetrievalService(
        evidence_repository,
        evidence_embedder,
        lexical_candidate_count=resolved.evidence_lexical_candidate_count,
        semantic_candidate_count=resolved.evidence_semantic_candidate_count,
        rrf_k=resolved.evidence_rrf_k,
        lexical_weight=resolved.evidence_rrf_lexical_weight,
        semantic_weight=resolved.evidence_rrf_semantic_weight,
        coverage_candidate_count=resolved.evidence_coverage_candidate_count,
        max_chunks_per_document=resolved.evidence_max_chunks_per_document,
    )
    evidence_ingestion = None
    if research_acquisition is not None:
        evidence_ingestion = EvidenceIngestionService(
            evidence_repository,
            MarkdownChunker(
                unit_spans=evidence_embedder.token_spans,
                unit_version=evidence_embedder.tokenization_version,
            ),
            evidence_embedder,
        )
    research = ResearchCoordinator(
        evidence_retrieval,
        evidence_coverage,
        query_planner,
        research_acquisition,
        evidence_ingestion,
        max_acquisition_rounds=resolved.research_max_acquisition_rounds,
        coverage_context_max=resolved.evidence_coverage_context_max,
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
        research=research,
        answers=answers,
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
        answer_synthesis=answer_synthesis,
        claim_verification=claim_verification,
        answers=answers,
        evidence_coverage=evidence_coverage,
        evidence_retrieval=evidence_retrieval,
        research=research,
        chat_interactions=chat_interactions,
    )
