from app.domain.validation import Disposition
from app.orchestration.state import ChatRoute, ChatState


REDIRECT_MESSAGE = (
    "I'm designed to help with politics, elections, government, public policy, "
    "civic participation, and public authority. This request falls outside that "
    "scope. You're welcome to ask a political or civic question instead."
)
REFUSE_MESSAGE = (
    "I can't help with that request. Please try asking a different political "
    "or civic question."
)
NEW_QUERY_MESSAGE = (
    "I couldn't produce a version of that question that fits the assistant's "
    "requirements without changing its meaning. Please ask it a different way."
)


def workflow_response(state: ChatState) -> str | None:
    if answer := state.get("answer_result"):
        return answer.text

    route = state.get("chat_route")
    if route is ChatRoute.AWAIT_CLARIFICATION:
        resolution = state.get("query_resolution")
        return resolution.clarification_question if resolution else None
    if route is ChatRoute.AWAIT_APPROVAL:
        proposal = state.get("query_reframe_proposal")
        return (
            f"I can help with this revised question: {proposal.proposed_query}\n\n"
            "Would you like me to proceed?"
            if proposal else None
        )
    if route is ChatRoute.NEW_QUERY_REQUIRED:
        return NEW_QUERY_MESSAGE

    gate = state.get("gate_result")
    if gate and gate.disposition is Disposition.REDIRECT:
        return REDIRECT_MESSAGE
    if gate and gate.disposition is Disposition.REFUSE:
        return REFUSE_MESSAGE
    return None
