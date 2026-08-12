from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domain.validation import Disposition
from app.features.input_validation.service import InputValidationService
from app.features.query_reframe.service import QueryReframeService
from app.features.query_resolution.service import QueryResolutionService
from app.observability.progress import ProgressEmitter
from app.orchestration.instrumentation import (
    AgentNode,
    instrument_node,
    log_route,
)
from app.orchestration.nodes import (
    build_input_preflight_node,
    build_input_validation_node,
    build_answer_node,
    build_query_reframe_node,
    build_query_resolution_node,
    build_research_node,
)
from app.orchestration.research import ResearchCoordinator
from app.orchestration.answer import AnswerCoordinator
from app.orchestration.state import ChatState
from app.roles import AgentRole


def build_chat_graph(
    input_validation: InputValidationService,
    query_reframe: QueryReframeService,
    query_resolution: QueryResolutionService,
    emitter: ProgressEmitter,
    research: ResearchCoordinator | None = None,
    answers: AnswerCoordinator | None = None,
) -> CompiledStateGraph:
    if answers is not None and research is None:
        raise ValueError("Answer coordination requires research")
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
    routes = {"query_reframe": "query_reframe", "end": END}
    if research is not None:
        _add_observed_node(
            graph,
            "research",
            AgentRole.QUERY_DIVERSIFIER,
            build_research_node(research),
            emitter,
        )
        if answers is None:
            graph.add_edge("research", END)
        else:
            _add_observed_node(
                graph,
                "answer",
                AgentRole.ANSWER_WRITER,
                build_answer_node(answers),
                emitter,
            )
            graph.add_edge("research", "answer")
            graph.add_edge("answer", END)
        routes["research"] = "research"
    graph.add_conditional_edges(
        "input_validation",
        lambda state: _route_after_input_validation(
            state,
            research_enabled=research is not None,
        ),
        routes,
    )
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


def _route_after_input_validation(
    state: ChatState,
    *,
    research_enabled: bool,
) -> str:
    if "proposal_gate_result" in state and "chat_route" not in state:
        return "query_reframe"
    if "chat_route" in state:
        return "end"
    disposition = state["gate_result"].disposition
    reasons = {
        Disposition.ALLOW: "original_allowed",
        Disposition.REFRAME: "original_requires_reframe",
        Disposition.REDIRECT: "original_out_of_scope",
        Disposition.REFUSE: "original_refused",
    }
    if disposition is Disposition.ALLOW:
        route = "research" if research_enabled else "end"
    elif disposition is Disposition.REFRAME:
        route = "query_reframe"
    else:
        route = "end"
    log_route(
        route,
        "research_available" if route == "research" else reasons[disposition],
        validation_disposition=disposition,
        validation_stage="original",
        validation_analysis=state["gate_result"].analysis,
    )
    return route


def _route_after_query_resolution(state: ChatState) -> str:
    return "end" if "chat_route" in state else "validate"


def _route_after_query_reframe(state: ChatState) -> str:
    return "end" if "chat_route" in state else "validate"
