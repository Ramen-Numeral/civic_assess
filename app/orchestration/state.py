from enum import StrEnum
from typing import NotRequired, TypedDict

from app.domain.acquisition import ResearchAcquisitionSet
from app.domain.conversation import ConversationContext
from app.domain.evidence import EvidenceIngestionSnapshot
from app.domain.research import ResearchQuerySet
from app.domain.validation import InputGateResult
from app.features.query_reframe.schemas import QueryReframeProposal
from app.features.query_resolution.schemas import QueryResolutionResult


class ChatRoute(StrEnum):
    AWAIT_APPROVAL = "await_approval"
    AWAIT_CLARIFICATION = "await_clarification"
    NEW_QUERY_REQUIRED = "new_query_required"


class ChatState(TypedDict):
    original_request: str
    conversation_context: ConversationContext
    normalized_request: NotRequired[str]
    query_resolution: NotRequired[QueryResolutionResult]
    gate_result: NotRequired[InputGateResult]
    research_query_set: NotRequired[ResearchQuerySet]
    research_acquisition: NotRequired[ResearchAcquisitionSet]
    evidence_ingestion: NotRequired[EvidenceIngestionSnapshot]
    query_reframe_proposal: NotRequired[QueryReframeProposal]
    proposal_gate_result: NotRequired[InputGateResult]
    chat_route: NotRequired[ChatRoute]
