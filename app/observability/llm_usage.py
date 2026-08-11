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


class InMemoryLLMUsageReporter(LLMUsageReporter):
    def __init__(self) -> None:
        self.attempts: list[LLMAttemptMetrics] = []

    def record(self, attempt: LLMAttemptMetrics) -> None:
        self.attempts.append(attempt)

    def for_run(self, run_id: str) -> tuple[LLMAttemptMetrics, ...]:
        return tuple(item for item in self.attempts if item.run_id == run_id)
