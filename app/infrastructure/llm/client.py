import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel

from app.infrastructure.llm.errors import (
    FALLBACK_FAILURES,
    LLMError,
    LLMFailure,
    classify_failure,
)
from app.roles import AgentRole
from config.llm import ModelCandidate


OutputT = TypeVar("OutputT", bound=BaseModel)
RouteOutputT = TypeVar("RouteOutputT", bound=AIMessage | BaseModel)
LOGGER = logging.getLogger(__name__)


class LLMClient(Protocol):
    async def invoke(self, messages: Sequence[BaseMessage]) -> AIMessage: ...

    async def invoke_structured(
        self, messages: Sequence[BaseMessage], output_schema: type[OutputT]
    ) -> OutputT: ...


@dataclass(frozen=True)
class RoutedModel:
    candidate: ModelCandidate
    client: BaseChatModel


class LLM:
    def __init__(self, role: AgentRole, models: Sequence[RoutedModel]):
        if not models:
            raise ValueError("LLM requires at least one model")
        self.role = role
        self.models = tuple(models)

    async def invoke(self, messages: Sequence[BaseMessage]) -> AIMessage:
        async def call(client: BaseChatModel) -> AIMessage:
            result = await client.ainvoke(messages)
            if not isinstance(result, AIMessage):
                raise TypeError("LLM did not return an AIMessage")
            return result

        return await self._invoke_route(call)

    async def invoke_structured(
        self,
        messages: Sequence[BaseMessage],
        output_schema: type[OutputT],
    ) -> OutputT:
        async def call(client: BaseChatModel) -> OutputT:
            result = await client.with_structured_output(output_schema).ainvoke(messages)
            return (
                result
                if isinstance(result, output_schema)
                else output_schema.model_validate(result)
            )

        return await self._invoke_route(call)

    async def _invoke_route(
        self,
        invoke_candidate: Callable[[BaseChatModel], Awaitable[RouteOutputT]],
    ) -> RouteOutputT:
        failures: list[LLMFailure] = []
        route_started = monotonic()
        for index, routed in enumerate(self.models):
            started = monotonic()
            try:
                result = await invoke_candidate(routed.client)
            except Exception as exc:
                failure = classify_failure(routed.candidate.provider, exc)
                failures.append(failure)
                fallback = failure.kind in FALLBACK_FAILURES
                LOGGER.warning(
                    "LLM candidate failed",
                    extra=self._attempt_fields(
                        routed,
                        index,
                        started,
                        outcome="failure",
                        failure_kind=failure.kind.value,
                        fallback_allowed=fallback,
                        exception_type=failure.exception_type,
                        status_code=failure.status_code,
                    ),
                )
                if not fallback:
                    raise
                last_error = exc
                if index + 1 < len(self.models):
                    LOGGER.warning(
                        "LLM fallback activated",
                        extra={
                            "event": "llm.fallback.activated",
                            "role": self.role.value,
                            "from_provider": routed.candidate.provider.value,
                            "from_model": routed.candidate.model,
                            "to_provider": self.models[index + 1].candidate.provider.value,
                            "to_model": self.models[index + 1].candidate.model,
                            "failure_kind": failure.kind.value,
                        },
                    )
            else:
                LOGGER.info(
                    "LLM candidate succeeded",
                    extra=self._attempt_fields(
                        routed, index, started, outcome="success"
                    ),
                )
                return result
        LOGGER.error(
            "LLM route exhausted",
            extra={
                "event": "llm.route.exhausted",
                "role": self.role.value,
                "attempt_count": len(failures),
                "failure_kinds": [failure.kind.value for failure in failures],
                "duration_ms": round((monotonic() - route_started) * 1000, 2),
            },
        )
        raise LLMError("Every configured model failed", tuple(failures)) from last_error

    def _attempt_fields(
        self, routed: RoutedModel, index: int, started: float, **fields: object,
    ) -> dict[str, object]:
        return {
            "event": "llm.attempt.completed",
            "role": self.role.value,
            "provider": routed.candidate.provider.value,
            "model": routed.candidate.model,
            "candidate_index": index,
            "duration_ms": round((monotonic() - started) * 1000, 2),
            **fields,
        }
