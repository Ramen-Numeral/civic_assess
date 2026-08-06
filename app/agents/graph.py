from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.nodes.input_validation import build_input_validation_node
from app.agents.state import ConversationState
from app.features.input_validation.service import InputValidationService


def build_input_validation_graph(
    service: InputValidationService,
) -> CompiledStateGraph[
    ConversationState,
    None,
    ConversationState,
    ConversationState,
]:
    graph = StateGraph(ConversationState)
    graph.add_node("input_validation", build_input_validation_node(service))
    graph.add_edge(START, "input_validation")
    graph.add_edge("input_validation", END)
    return graph.compile(name="input_validation")
