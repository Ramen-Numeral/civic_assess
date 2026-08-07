from enum import StrEnum
from typing import NotRequired, TypedDict

from app.domain.conversation import ConversationTurn
from app.domain.validation import InputGateResult
from app.features.query_reframe.schemas import QueryReframeProposal
from app.features.query_resolution.schemas import QueryResolutionResult


class ChatRoute(StrEnum):
    AWAIT_APPROVAL = "await_approval"
    AWAIT_CLARIFICATION = "await_clarification"
    NEW_QUERY_REQUIRED = "new_query_required"


class ChatState(TypedDict):
    original_request: str
    normalized_request: NotRequired[str]
    recent_turns: NotRequired[tuple[ConversationTurn, ...]]
    query_resolution: NotRequired[QueryResolutionResult]
    gate_result: NotRequired[InputGateResult]
    query_reframe_proposal: NotRequired[QueryReframeProposal]
    proposal_gate_result: NotRequired[InputGateResult]
    chat_route: NotRequired[ChatRoute]
