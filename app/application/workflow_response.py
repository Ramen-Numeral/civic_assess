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
REFRAME_APPROVAL_PREFIX = "I can help with this revised question:\n\n"
REFRAME_APPROVAL_SUFFIX = (
    "\n\nIf you would like me to proceed with this query, please say yes."
)


def reframe_approval_response(proposed_query: str) -> str:
    return f"{REFRAME_APPROVAL_PREFIX}{proposed_query}{REFRAME_APPROVAL_SUFFIX}"


def pending_reframe_query(response: str) -> str | None:
    if not (
        response.startswith(REFRAME_APPROVAL_PREFIX)
        and response.endswith(REFRAME_APPROVAL_SUFFIX)
    ):
        return None
    proposed_query = response[
        len(REFRAME_APPROVAL_PREFIX) : -len(REFRAME_APPROVAL_SUFFIX)
    ].strip()
    return proposed_query or None


def workflow_response(state: ChatState) -> str | None:
    if answer := state.get("answer_result"):
        return answer.text

    route = state.get("chat_route")
    if route is ChatRoute.AWAIT_CLARIFICATION:
        resolution = state.get("query_resolution")
        return resolution.clarification_question if resolution else None
    if route is ChatRoute.AWAIT_APPROVAL:
        proposal = state.get("query_reframe_proposal")
        return reframe_approval_response(proposal.proposed_query) if proposal else None
    if route is ChatRoute.REFRAME_DECLINED:
        return REFUSE_MESSAGE
    if route is ChatRoute.NEW_QUERY_REQUIRED:
        return NEW_QUERY_MESSAGE

    gate = state.get("gate_result")
    if gate and gate.disposition is Disposition.REDIRECT:
        return REDIRECT_MESSAGE
    if gate and gate.disposition is Disposition.REFUSE:
        return REFUSE_MESSAGE
    return None
