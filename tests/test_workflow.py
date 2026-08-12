import asyncio
from uuid import uuid4

import pytest

from app.application.workflow_response import REDIRECT_MESSAGE, workflow_response
from app.domain.conversation import (
    ConversationContext,
    ConversationContextStatus,
    ConversationRole,
    ConversationTurn,
)
from app.domain.validation import (
    BehaviorAssessment,
    Disposition,
    InputGateAnalysis,
    InputGateResult,
    InstructionIntegrityAssessment,
    ScopeAssessment,
)
from app.features.input_validation.schemas import InputValidationRequest
from app.features.query_reframe.schemas import QueryReframeMode, QueryReframeProposal
from app.features.query_resolution.schemas import QueryResolutionResult
from app.orchestration.orchestrator import ChatOrchestrator
from app.orchestration.state import ChatRoute

pytestmark = [pytest.mark.e2e, pytest.mark.smoke]


def gate(disposition: Disposition, *, behavior=BehaviorAssessment.ALLOWED):
    return InputGateResult(
        analysis=InputGateAnalysis(
            instruction_integrity=InstructionIntegrityAssessment.NONE,
            scope=ScopeAssessment.IN_SCOPE,
            behavior=behavior,
        ),
        disposition=disposition,
        normalized_query="original query",
    )


class Validator:
    def __init__(self, *results):
        self.results = list(results)

    async def validate(self, request):
        return self.results.pop(0)


class Reframer:
    def __init__(self):
        self.mode = None

    async def reframe(self, request):
        self.mode = request.mode
        return QueryReframeProposal(proposed_query="neutral query")


class Resolver:
    async def resolve(self, request):
        return QueryResolutionResult(resolved_query=request.normalized_query)


def invoke(validator, reframer):
    return asyncio.run(
        ChatOrchestrator(validator, reframer, Resolver()).invoke(
            InputValidationRequest(original_query="original query"),
            conversation_context=ConversationContext(
                conversation_id=uuid4(),
                current_turn_id=uuid4(),
                status=ConversationContextStatus.RECENT_ONLY,
            ),
        )
    )


@pytest.mark.parametrize(
    "disposition", [Disposition.ALLOW, Disposition.REDIRECT, Disposition.REFUSE]
)
def test_terminal_gate_dispositions_do_not_reframe(disposition) -> None:
    reframer = Reframer()
    state = invoke(Validator(gate(disposition)), reframer)
    assert state["gate_result"].disposition is disposition
    assert reframer.mode is None


@pytest.mark.regression
def test_neutral_reframe_is_revalidated_and_awaits_approval() -> None:
    reframer = Reframer()
    state = invoke(
        Validator(
            gate(
                Disposition.REFRAME,
                behavior=BehaviorAssessment.REQUIRES_NEUTRAL_REFRAME,
            ),
            InputGateResult(
                analysis=gate(Disposition.ALLOW).analysis,
                disposition=Disposition.ALLOW,
                normalized_query="neutral query",
            ),
        ),
        reframer,
    )

    assert reframer.mode is QueryReframeMode.NEUTRAL
    assert state["proposal_gate_result"].disposition is Disposition.ALLOW
    assert state["chat_route"] is ChatRoute.AWAIT_APPROVAL


@pytest.mark.regression
def test_resolved_follow_up_preserves_original_for_safety_validation() -> None:
    original = "Why was he racist?"
    resolved = "Why was J. Edgar Hoover considered racist?"
    validator = Validator(
        InputGateResult(
            analysis=gate(
                Disposition.REFRAME,
                behavior=BehaviorAssessment.REQUIRES_NEUTRAL_REFRAME,
            ).analysis,
            disposition=Disposition.REFRAME,
            normalized_query=resolved,
        ),
        InputGateResult(
            analysis=gate(Disposition.ALLOW).analysis,
            disposition=Disposition.ALLOW,
            normalized_query="What factors shaped Hoover's policies regarding race?",
        ),
    )
    requests = []
    original_validate = validator.validate

    async def validate(request):
        requests.append(request)
        return await original_validate(request)

    validator.validate = validate

    class ContextResolver:
        async def resolve(self, request):
            return QueryResolutionResult(resolved_query=resolved)

    context = ConversationContext(
        conversation_id=uuid4(),
        current_turn_id=uuid4(),
        recent_turns=(
            ConversationTurn(
                turn_id=uuid4(),
                role=ConversationRole.ASSISTANT,
                content="J. Edgar Hoover led the FBI.",
            ),
        ),
        status=ConversationContextStatus.RECENT_ONLY,
    )
    state = asyncio.run(
        ChatOrchestrator(
            validator,
            Reframer(),
            ContextResolver(),
        ).invoke(
            InputValidationRequest(original_query=original),
            conversation_context=context,
        )
    )

    assert requests[0].original_query == original
    assert requests[0].resolved_query == resolved
    assert state["chat_route"] is ChatRoute.AWAIT_APPROVAL


@pytest.mark.regression
@pytest.mark.parametrize(
    ("disposition", "expected_route", "expected_response"),
    [
        (Disposition.ALLOW, ChatRoute.AWAIT_CLARIFICATION, "Which policy do you mean?"),
        (Disposition.REDIRECT, None, REDIRECT_MESSAGE),
    ],
)
def test_resolver_clarification_cannot_bypass_validation(
    disposition, expected_route, expected_response
) -> None:
    class ClarifyingResolver:
        async def resolve(self, request):
            return QueryResolutionResult(
                clarification_question="Which policy do you mean?"
            )

    context = ConversationContext(
        conversation_id=uuid4(),
        current_turn_id=uuid4(),
        recent_turns=(
            ConversationTurn(
                turn_id=uuid4(),
                role=ConversationRole.ASSISTANT,
                content="Earlier context.",
            ),
        ),
        status=ConversationContextStatus.RECENT_ONLY,
    )
    state = asyncio.run(
        ChatOrchestrator(
            Validator(gate(disposition)),
            Reframer(),
            ClarifyingResolver(),
        ).invoke(
            InputValidationRequest(original_query="Can you help with this?"),
            conversation_context=context,
        )
    )

    assert state.get("chat_route") is expected_route
    assert workflow_response(state) == expected_response
