from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.instrumentation import instrument_node
from app.agents.nodes.input_validation import build_input_validation_node
from app.agents.state import ConversationState
from app.features.input_validation.service import InputValidationService
from app.observability.progress import ProgressEmitter
from app.roles import AgentRole


def build_input_validation_graph(
    service: InputValidationService,
    emitter: ProgressEmitter,
) -> CompiledStateGraph[
    ConversationState,
    None,
    ConversationState,
    ConversationState,
]:
    graph = StateGraph(ConversationState)
    graph.add_node(
        "input_validation",
        instrument_node(
            phase=AgentRole.VALIDATOR,
            node=build_input_validation_node(service),
            emitter=emitter,
        ),
    )
    graph.add_edge(START, "input_validation")
    graph.add_edge("input_validation", END)
    return graph.compile(name="input_validation")
