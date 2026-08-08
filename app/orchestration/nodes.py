from app.domain.validation import Disposition
from app.features.evidence_ingestion.service import EvidenceIngestionService
from app.features.query_diversification.schemas import QueryDiversificationRequest
from app.features.query_diversification.service import QueryDiversificationService
from app.features.input_validation.errors import InputValidationError
from app.features.input_validation.preflight import preflight_input
from app.features.input_validation.schemas import InputValidationRequest
from app.features.input_validation.service import InputValidationService
from app.features.query_reframe.errors import InvalidReframeProposalError
from app.features.query_reframe.schemas import (
    QueryReframeProposal,
    QueryReframeRequest,
)
from app.features.query_reframe.service import (
    QueryReframeService,
    determine_reframe_mode,
)
from app.features.query_resolution.schemas import QueryResolutionRequest
from app.features.query_resolution.service import QueryResolutionService
from app.features.research_acquisition.service import ResearchAcquisitionService
from app.orchestration.instrumentation import AgentNode, log_route
from app.orchestration.state import ChatRoute, ChatState


def build_input_preflight_node() -> AgentNode[ChatState]:
    async def preflight(state: ChatState) -> dict[str, object]:
        normalized = preflight_input(
            InputValidationRequest(query=state["original_request"])
        )
        return {"normalized_request": normalized}

    return preflight


def build_input_validation_node(
    service: InputValidationService,
) -> AgentNode[ChatState]:
    async def validate_input(state: ChatState) -> dict[str, object]:
        proposal = state.get("query_reframe_proposal")
        query = (
            proposal.proposed_query
            if proposal is not None
            else state["query_resolution"].resolved_query
        )
        if query is None:
            raise RuntimeError("Input validation requires a resolved query")
        try:
            result = await service.validate(InputValidationRequest(query=query))
        except InputValidationError as exc:
            if proposal is None or exc.retryable:
                raise
            route = ChatRoute.NEW_QUERY_REQUIRED
            log_route(route, "proposal_input_invalid")
            return {"chat_route": route}

        if proposal is None:
            return {"gate_result": result}

        original = state["gate_result"].normalized_query
        approved = (
            result.disposition is Disposition.ALLOW
            and result.normalized_query != original
        )
        route = (
            ChatRoute.AWAIT_APPROVAL
            if approved
            else ChatRoute.NEW_QUERY_REQUIRED
        )
        log_route(
            route,
            "proposal_allowed" if approved else "proposal_rejected",
            validation_disposition=result.disposition,
        )
        update: dict[str, object] = {
            "proposal_gate_result": result,
            "chat_route": route,
        }
        if approved:
            update["query_reframe_proposal"] = QueryReframeProposal(
                proposed_query=result.normalized_query
            )
        return update

    return validate_input


def build_query_resolution_node(
    service: QueryResolutionService,
) -> AgentNode[ChatState]:
    async def resolve_query(state: ChatState) -> dict[str, object]:
        result = await service.resolve(
            QueryResolutionRequest(
                normalized_query=state["normalized_request"],
                context=state["conversation_context"],
            )
        )
        if result.clarification_question is not None:
            route = ChatRoute.AWAIT_CLARIFICATION
            log_route(route, "context_ambiguous")
            return {
                "query_resolution": result,
                "chat_route": route,
            }
        return {"query_resolution": result}

    return resolve_query


def build_query_diversification_node(
    service: QueryDiversificationService,
) -> AgentNode[ChatState]:
    async def diversify_query(state: ChatState) -> dict[str, object]:
        result = await service.diversify(
            QueryDiversificationRequest(
                validated_query=state["gate_result"].normalized_query,
            )
        )
        return {"research_query_set": result}

    return diversify_query


def build_research_acquisition_node(
    service: ResearchAcquisitionService,
) -> AgentNode[ChatState]:
    async def acquire_research(state: ChatState) -> dict[str, object]:
        result = await service.acquire(state["research_query_set"])
        return {"research_acquisition": result}

    return acquire_research


def build_evidence_ingestion_node(
    service: EvidenceIngestionService,
) -> AgentNode[ChatState]:
    async def ingest_evidence(state: ChatState) -> dict[str, object]:
        result = await service.ingest(
            conversation_id=state["conversation_context"].conversation_id,
            query_set=state["research_query_set"],
            acquisition=state["research_acquisition"],
        )
        return {"evidence_ingestion": result}

    return ingest_evidence


def build_query_reframe_node(
    service: QueryReframeService,
) -> AgentNode[ChatState]:
    async def reframe_query(state: ChatState) -> dict[str, object]:
        gate_result = state["gate_result"]
        mode = determine_reframe_mode(gate_result.analysis)
        try:
            proposal = await service.reframe(
                QueryReframeRequest(
                    normalized_query=gate_result.normalized_query,
                    analysis=gate_result.analysis,
                    mode=mode,
                )
            )
        except InvalidReframeProposalError:
            route = ChatRoute.NEW_QUERY_REQUIRED
            log_route(route, "proposal_write_invalid", mode=mode)
            return {"chat_route": route}
        return {"query_reframe_proposal": proposal}

    return reframe_query
