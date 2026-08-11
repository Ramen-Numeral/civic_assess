from app.domain.validation import Disposition
from app.features.input_validation.errors import InputValidationError
from app.features.input_validation.preflight import preflight_input
from app.features.input_validation.schemas import InputValidationRequest
from app.features.input_validation.service import InputValidationService
from app.features.answer_synthesis.schemas import AnswerFinding, GroundedAnswerRequest
from app.features.query_reframe.errors import InvalidReframeProposalError
from app.features.query_reframe.schemas import (
    QueryReframeProposal,
    QueryReframeRequest,
)
from app.features.query_reframe.service import (
    QueryReframeService,
    determine_reframe_mode,
)
from app.features.query_resolution.schemas import (
    QueryResolutionRequest,
    QueryResolutionResult,
)
from app.features.query_resolution.service import QueryResolutionService
from app.orchestration.instrumentation import AgentNode, log_route
from app.orchestration.answer import AnswerCoordinator
from app.orchestration.research import ResearchCoordinator
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
        context = state["conversation_context"]
        if not context.recent_turns and context.state is None:
            return {"query_resolution": QueryResolutionResult(
                resolved_query=state["normalized_request"],
            )}
        result = await service.resolve(
            QueryResolutionRequest(
                normalized_query=state["normalized_request"],
                context=context,
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


def build_research_node(
    coordinator: ResearchCoordinator,
) -> AgentNode[ChatState]:
    async def research(state: ChatState) -> dict[str, object]:
        result = await coordinator.research(
            conversation_id=state["conversation_context"].conversation_id,
            canonical_query=state["gate_result"].normalized_query,
        )
        return {"research_result": result}

    return research


def build_answer_node(coordinator: AnswerCoordinator) -> AgentNode[ChatState]:
    async def answer(state: ChatState) -> dict[str, object]:
        research = state["research_result"]
        result = await coordinator.answer(GroundedAnswerRequest(
            canonical_query=state["gate_result"].normalized_query,
            temporal_scope=research.plan.temporal_scope,
            findings=tuple(AnswerFinding(
                statement=item.statement,
                supporting_chunk_ids=item.supporting_chunk_ids,
                evidence_basis=item.evidence_basis,
                source_fitness=item.source_fitness,
                qualification=item.qualification,
            ) for item in research.cumulative_coverage.findings),
            evidence=research.cumulative_evidence,
            unresolved=tuple(dict.fromkeys(
                item.missing_evidence
                for item in research.cumulative_coverage.gaps
            )),
        ))
        return {"answer_result": result}

    return answer


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
