import json
import logging
from collections.abc import Mapping

from langchain_core.messages import HumanMessage, SystemMessage

from app.domain.validation import (
    BehaviorAssessment,
    InputGateAnalysis,
    InstructionIntegrityAssessment,
)
from app.features.query_reframe.errors import (
    InvalidReframeProposalError,
    QueryReframeError,
)
from app.features.query_reframe.schemas import (
    QueryReframeMode,
    QueryReframeProposal,
    QueryReframeRequest,
)
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.errors import FailureKind, LLMError
from app.prompts.base import Prompt

LOGGER = logging.getLogger(__name__)


def determine_reframe_mode(analysis: InputGateAnalysis) -> QueryReframeMode:
    if analysis.instruction_integrity is InstructionIntegrityAssessment.AMBIGUOUS:
        return QueryReframeMode.INTEGRITY_CLARIFICATION
    if analysis.behavior is BehaviorAssessment.REQUIRES_SAFETY_REFRAME:
        return QueryReframeMode.SAFETY
    if analysis.behavior is BehaviorAssessment.REQUIRES_NEUTRAL_REFRAME:
        return QueryReframeMode.NEUTRAL
    raise RuntimeError("REFRAME disposition has no reframe mode")


class QueryReframeService:
    def __init__(
        self,
        *,
        llm: LLMClient,
        prompts: Mapping[QueryReframeMode, Prompt],
    ) -> None:
        if set(prompts) != set(QueryReframeMode):
            raise ValueError("Every query-reframe mode requires a prompt")
        self._llm = llm
        self._prompts = prompts

    async def reframe(
        self,
        request: QueryReframeRequest,
    ) -> QueryReframeProposal:
        prompt = self._prompts[request.mode]
        messages = [
            SystemMessage(content=prompt.build()),
            HumanMessage(
                content=json.dumps(
                    {
                        "original_query": request.original_query,
                        "resolved_query": request.resolved_query,
                        "gate_analysis": request.analysis.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                )
            ),
        ]

        try:
            proposal = await self._llm.invoke_structured(
                messages,
                QueryReframeProposal,
            )
            LOGGER.info(
                "Query reframe proposal produced",
                extra={
                    "event": "query_reframe.proposal",
                    "mode": request.mode.value,
                    "original_query": request.original_query,
                    "resolved_query": request.resolved_query,
                    "proposed_query": proposal.proposed_query,
                },
            )
            return proposal
        except LLMError as exc:
            if exc.failures and all(
                failure.kind is FailureKind.INVALID_OUTPUT for failure in exc.failures
            ):
                raise InvalidReframeProposalError(
                    "Reframer returned invalid structured output"
                ) from exc
            raise QueryReframeError(
                "reframer_unavailable",
                "I can't reframe that request right now. Please try again.",
                retryable=True,
            ) from exc
