from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.application.chat_interaction import ChatInteractionResult
from app.domain.validation import Disposition, InputGateAnalysis, InputGateResult
from app.features.query_reframe.schemas import QueryReframeProposal
from app.observability.llm_usage import LLMAttemptMetrics
from app.orchestration.state import ChatState


class RequestRoutingCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    query: str
    expected: InputGateAnalysis
    expected_disposition: Disposition


class TurnExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal[
        "answer", "redirect", "refuse", "await_approval", "await_clarification"
    ]
    minimum_sources: int = Field(default=0, ge=0)
    minimum_answer_quality: int = Field(default=0, ge=0, le=5)
    maximum_unresolved_gaps: int | None = Field(default=None, ge=0)
    minimum_evidence_angles: int = Field(default=0, ge=0)


class RubricScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    title: str
    turns: tuple[str, ...] = Field(min_length=1)
    expectations: tuple[TurnExpectation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_expectation_per_turn(self) -> "RubricScenario":
        if len(self.turns) != len(self.expectations):
            raise ValueError("Each scenario turn requires one expectation")
        return self


@dataclass(frozen=True)
class RequestRoutingRun:
    case: RequestRoutingCase
    result: InputGateResult
    run_number: int
    wall_clock_ms: float
    usage: tuple[LLMAttemptMetrics, ...]
    reframe_proposal: QueryReframeProposal | None = None


@dataclass(frozen=True)
class ScenarioTurn:
    query: str
    expectation: TurnExpectation
    result: ChatInteractionResult | None
    wall_clock_ms: float
    error_code: str | None = None
    error_message: str | None = None
    workflow_state: ChatState | None = None


@dataclass(frozen=True)
class RubricRun:
    scenario: RubricScenario
    turns: tuple[ScenarioTurn, ...]
    run_number: int
    wall_clock_ms: float
    usage: tuple[LLMAttemptMetrics, ...]
