from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import anthropic
import groq
import openai
from pydantic import ValidationError

from config.llm import ModelProvider


class FailureKind(StrEnum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    INVALID_OUTPUT = "invalid_output"
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    UNKNOWN = "unknown"


FALLBACK_FAILURES = frozenset({
    FailureKind.TIMEOUT,
    FailureKind.CONNECTION,
    FailureKind.RATE_LIMIT,
    FailureKind.UNAVAILABLE,
    FailureKind.INVALID_OUTPUT,
})


@dataclass(frozen=True)
class LLMFailure:
    kind: FailureKind
    exception_type: str
    status_code: int | None = None


class ProviderSDK(Protocol):
    APITimeoutError: type[Exception]
    APIConnectionError: type[Exception]
    RateLimitError: type[Exception]
    APIResponseValidationError: type[Exception]
    APIStatusError: type[Exception]


SDKS: dict[ModelProvider, ProviderSDK] = {
    ModelProvider.GROQ: groq,
    ModelProvider.OPENAI: openai,
    ModelProvider.ANTHROPIC: anthropic,
}


def classify_failure(provider: ModelProvider, exc: Exception) -> LLMFailure:
    kind: FailureKind
    status = getattr(exc, "status_code", None)
    if isinstance(exc, (TimeoutError, SDKS[provider].APITimeoutError)):
        kind = FailureKind.TIMEOUT
    elif isinstance(exc, (ConnectionError, SDKS[provider].APIConnectionError)):
        kind = FailureKind.CONNECTION
    elif isinstance(exc, SDKS[provider].RateLimitError):
        kind = FailureKind.RATE_LIMIT
    elif isinstance(exc, (ValidationError, SDKS[provider].APIResponseValidationError)):
        kind = FailureKind.INVALID_OUTPUT
    elif provider is ModelProvider.ANTHROPIC and isinstance(exc, anthropic.RetryableError):
        kind = FailureKind.UNAVAILABLE
    elif isinstance(exc, SDKS[provider].APIStatusError):
        if status == 408:
            kind = FailureKind.TIMEOUT
        elif status == 429:
            kind = FailureKind.RATE_LIMIT
        elif status == 409 or isinstance(status, int) and status >= 500:
            kind = FailureKind.UNAVAILABLE
        elif status in {401, 403}:
            kind = FailureKind.AUTHENTICATION
        elif isinstance(status, int) and 400 <= status < 500:
            kind = FailureKind.INVALID_REQUEST
        else:
            kind = FailureKind.UNKNOWN
    else:
        kind = FailureKind.UNKNOWN
    return LLMFailure(kind, type(exc).__name__, status if isinstance(status, int) else None)


class LLMError(RuntimeError):
    """Base error for model invocation failures."""

    def __init__(self, message: str, failures: tuple[LLMFailure, ...] = ()) -> None:
        super().__init__(message)
        self.failures = failures
