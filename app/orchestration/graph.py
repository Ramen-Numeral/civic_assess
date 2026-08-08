from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domain.validation import Disposition
from app.features.input_validation.service import InputValidationService
from app.features.evidence_ingestion.service import EvidenceIngestionService
from app.features.query_diversification.service import QueryDiversificationService
from app.features.query_reframe.service import QueryReframeService
from app.features.query_resolution.service import QueryResolutionService
from app.features.research_acquisition.service import ResearchAcquisitionService
from app.observability.progress import ProgressEmitter
from app.orchestration.instrumentation import AgentNode, instrument_node, log_route
from app.orchestration.nodes import (
    build_input_preflight_node,
    build_evidence_ingestion_node,
    build_input_validation_node,
    build_query_diversification_node,
    build_query_reframe_node,
    build_query_resolution_node,
    build_research_acquisition_node,
)
from app.orchestration.state import ChatState
from app.roles import AgentRole


def build_chat_graph(
    input_validation: InputValidationService,
    query_reframe: QueryReframeService,
    query_resolution: QueryResolutionService,
    query_diversification: QueryDiversificationService,
    emitter: ProgressEmitter,
    research_acquisition: ResearchAcquisitionService | None = None,
    evidence_ingestion: EvidenceIngestionService | None = None,
) -> CompiledStateGraph:
    if evidence_ingestion is not None and research_acquisition is None:
        raise ValueError("evidence ingestion requires research acquisition")
    graph = StateGraph(ChatState)
    graph.add_node("input_preflight", build_input_preflight_node())
    _add_observed_node(
        graph,
        "query_resolution",
        AgentRole.QUERY_RESOLVER,
        build_query_resolution_node(query_resolution),
        emitter,
    )
    _add_observed_node(
        graph,
        "query_diversification",
        AgentRole.QUERY_DIVERSIFIER,
        build_query_diversification_node(query_diversification),
        emitter,
    )
    _add_observed_node(
        graph,
        "input_validation",
        AgentRole.VALIDATOR,
        build_input_validation_node(input_validation),
        emitter,
    )
    _add_observed_node(
        graph,
        "query_reframe",
        AgentRole.REWRITER,
        build_query_reframe_node(query_reframe),
        emitter,
    )
    graph.add_edge(START, "input_preflight")
    graph.add_edge("input_preflight", "query_resolution")
    graph.add_conditional_edges(
        "query_resolution",
        _route_after_query_resolution,
        {"validate": "input_validation", "end": END},
    )
    graph.add_conditional_edges(
        "input_validation",
        _route_after_input_validation,
        {
            "query_diversification": "query_diversification",
            "query_reframe": "query_reframe",
            "end": END,
        },
    )
    if research_acquisition is None:
        graph.add_edge("query_diversification", END)
    else:
        graph.add_node(
            "research_acquisition",
            build_research_acquisition_node(research_acquisition),
        )
        graph.add_edge("query_diversification", "research_acquisition")
        if evidence_ingestion is None:
            graph.add_edge("research_acquisition", END)
        else:
            graph.add_node(
                "evidence_ingestion",
                build_evidence_ingestion_node(evidence_ingestion),
            )
            graph.add_edge("research_acquisition", "evidence_ingestion")
            graph.add_edge("evidence_ingestion", END)
    graph.add_conditional_edges(
        "query_reframe",
        _route_after_query_reframe,
        {"validate": "input_validation", "end": END},
    )
    return graph.compile(name="chat")


def _add_observed_node(
    graph: StateGraph,
    name: str,
    phase: AgentRole,
    node: AgentNode[ChatState],
    emitter: ProgressEmitter,
) -> None:
    graph.add_node(
        name,
        instrument_node(phase=phase, node=node, emitter=emitter),
    )


def _route_after_input_validation(state: ChatState) -> str:
    if "proposal_gate_result" in state or "chat_route" in state:
        return "end"
    disposition = state["gate_result"].disposition
    reasons = {
        Disposition.ALLOW: "original_allowed",
        Disposition.REFRAME: "original_requires_reframe",
        Disposition.REDIRECT: "original_out_of_scope",
        Disposition.REFUSE: "original_refused",
    }
    if disposition is Disposition.ALLOW:
        route = "query_diversification"
    elif disposition is Disposition.REFRAME:
        route = "query_reframe"
    else:
        route = "end"
    log_route(
        route,
        reasons[disposition],
        validation_disposition=disposition,
    )
    return route


def _route_after_query_resolution(state: ChatState) -> str:
    return "end" if "chat_route" in state else "validate"


def _route_after_query_reframe(state: ChatState) -> str:
    return "end" if "chat_route" in state else "validate"
