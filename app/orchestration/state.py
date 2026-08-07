from enum import StrEnum
from typing import NotRequired, TypedDict

from app.domain.validation import InputGateResult
from app.features.query_reframe.schemas import QueryReframeProposal


class ChatRoute(StrEnum):
    AWAIT_APPROVAL = "await_approval"
    NEW_QUERY_REQUIRED = "new_query_required"


class ChatState(TypedDict):
    original_request: str
    normalized_request: NotRequired[str]
    gate_result: NotRequired[InputGateResult]
    query_reframe_proposal: NotRequired[QueryReframeProposal]
    proposal_gate_result: NotRequired[InputGateResult]
    chat_route: NotRequired[ChatRoute]
