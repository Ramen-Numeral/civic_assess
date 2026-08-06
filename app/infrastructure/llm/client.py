from collections.abc import Sequence
from typing import Protocol, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel, ValidationError

from app.infrastructure.llm.errors import LLMError


OutputT = TypeVar("OutputT", bound=BaseModel)


class LLMClient(Protocol):
    async def invoke(self, messages: Sequence[BaseMessage]) -> AIMessage: ...

    async def invoke_structured(
        self, messages: Sequence[BaseMessage], output_schema: type[OutputT]
    ) -> OutputT: ...


def _fallback_allowed(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.lower()
    return (
        isinstance(exc, (TimeoutError, ConnectionError, ValidationError))
        or status in {408, 409, 429}
        or isinstance(status, int) and status >= 500
        or any(word in name for word in ("timeout", "connection", "ratelimit"))
    )


class LLM:
    def __init__(self, models: Sequence[BaseChatModel]):
        if not models:
            raise ValueError("LLM requires at least one model")
        self.models = tuple(models)

    async def invoke(self, messages: Sequence[BaseMessage]) -> AIMessage:
        for model in self.models:
            try:
                result = await model.ainvoke(messages)
                if not isinstance(result, AIMessage):
                    raise TypeError("LLM did not return an AIMessage")
                return result
            except Exception as exc:
                if not _fallback_allowed(exc):
                    raise
                last_error = exc
        raise LLMError("Every configured model failed") from last_error

    async def invoke_structured(
        self,
        messages: Sequence[BaseMessage],
        output_schema: type[OutputT],
    ) -> OutputT:
        for model in self.models:
            try:
                result = await model.with_structured_output(output_schema).ainvoke(messages)
                return result if isinstance(result, output_schema) else output_schema.model_validate(result)
            except Exception as exc:
                if not _fallback_allowed(exc):
                    raise
                last_error = exc
        raise LLMError("Every configured model failed") from last_error
