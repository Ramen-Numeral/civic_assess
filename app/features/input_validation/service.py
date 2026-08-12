import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.domain.validation import (
    BehaviorAssessment,
    Disposition,
    InputGateAnalysis,
    InputGateResult,
    InstructionIntegrityAssessment,
    ScopeAssessment,
)
from app.features.input_validation.errors import InputValidationError
from app.features.input_validation.preflight import MAX_QUERY_LENGTH, normalize_query
from app.features.input_validation.schemas import InputValidationRequest
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.errors import LLMError
from app.prompts.base import Prompt


def determine_disposition(analysis: InputGateAnalysis) -> Disposition:
    if (
        analysis.instruction_integrity
        is InstructionIntegrityAssessment.ACTIVE_INSTRUCTION_MANIPULATION
    ):
        return Disposition.REFUSE
    if analysis.scope is ScopeAssessment.OUT_OF_SCOPE:
        return Disposition.REDIRECT
    if analysis.behavior is BehaviorAssessment.DISALLOWED:
        return Disposition.REFUSE
    if (
        analysis.instruction_integrity
        is InstructionIntegrityAssessment.AMBIGUOUS
        or analysis.behavior
        in {
            BehaviorAssessment.REQUIRES_NEUTRAL_REFRAME,
            BehaviorAssessment.REQUIRES_SAFETY_REFRAME,
        }
    ):
        return Disposition.REFRAME
    return Disposition.ALLOW


class InputValidationService:
    def __init__(self, *, llm: LLMClient, prompt: Prompt) -> None:
        self._llm = llm
        self._prompt = prompt

    async def validate(self, request: InputValidationRequest) -> InputGateResult:
        original_query = normalize_query(request.original_query, MAX_QUERY_LENGTH)
        resolved_query = normalize_query(
            request.resolved_query or original_query,
            MAX_QUERY_LENGTH,
        )

        messages = [
            SystemMessage(content=self._prompt.build()),
            HumanMessage(
                content=json.dumps(
                    {
                        "original_query": original_query,
                        "resolved_query": resolved_query,
                    },
                    ensure_ascii=False,
                )
            ),
        ]

        try:
            analysis = await self._llm.invoke_structured(messages, InputGateAnalysis)
        except LLMError as exc:
            raise InputValidationError(
                "validator_unavailable",
                "I can't validate that request right now. Please try again.",
                retryable=True,
            ) from exc

        return InputGateResult(
            analysis=analysis,
            disposition=determine_disposition(analysis),
            normalized_query=resolved_query,
        )
