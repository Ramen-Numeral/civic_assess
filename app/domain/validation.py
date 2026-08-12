from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class InstructionIntegrityAssessment(StrEnum):
    NONE = "none"
    AMBIGUOUS = "ambiguous"
    ACTIVE_INSTRUCTION_MANIPULATION = "active_instruction_manipulation"


class ScopeAssessment(StrEnum):
    IN_SCOPE = "in_scope"
    ADJACENT = "adjacent"
    OUT_OF_SCOPE = "out_of_scope"


class BehaviorAssessment(StrEnum):
    ALLOWED = "allowed"
    REQUIRES_NEUTRAL_REFRAME = "requires_neutral_reframe"
    REQUIRES_SAFETY_REFRAME = "requires_safety_reframe"
    DISALLOWED = "disallowed"


class Disposition(StrEnum):
    ALLOW = "allow"
    REFRAME = "reframe"
    REDIRECT = "redirect"
    REFUSE = "refuse"


class InputGateAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instruction_integrity: InstructionIntegrityAssessment
    scope: ScopeAssessment
    behavior: BehaviorAssessment


class InputGateResult(BaseModel):
    """A gate decision and normalized original input, not execution authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis: InputGateAnalysis
    disposition: Disposition
    normalized_query: NonBlankText
