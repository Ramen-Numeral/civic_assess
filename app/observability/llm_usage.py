from dataclasses import dataclass, field
from enum import StrEnum

from app.observability.context import current_run_id
from app.roles import AgentRole


class TokenSource(StrEnum):
    PROVIDER_REPORTED = "provider_reported"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class LLMAttemptMetrics:
    role: AgentRole
    provider: str
    model: str
    candidate_index: int
    duration_ms: float
    outcome: str
    fallback_triggered: bool = False
    error_code: str | None = None
    run_id: str | None = field(default_factory=current_run_id)
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    token_source: TokenSource = TokenSource.UNAVAILABLE


class LLMUsageReporter:
    def record(self, attempt: LLMAttemptMetrics) -> None:
        raise NotImplementedError


class NoOpLLMUsageReporter(LLMUsageReporter):
    def record(self, attempt: LLMAttemptMetrics) -> None:
        pass
