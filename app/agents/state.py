from typing import NotRequired, TypedDict

from app.domain.validation import InputGateResult


class ConversationState(TypedDict):
    original_request: str
    normalized_query: NotRequired[str]
    gate_result: NotRequired[InputGateResult]
