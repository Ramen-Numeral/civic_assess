import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel

from app.features.input_validation.errors import InputValidationError
from app.features.input_validation.preflight import preflight_input
from app.features.input_validation.schemas import InputValidationRequest
from app.infrastructure.llm.client import LLM, RoutedModel
from app.infrastructure.llm.errors import LLMError
from app.roles import AgentRole
from config.llm import ModelCandidate, ModelProvider
from config.settings import load_application_config


class Result(BaseModel):
    value: str


class ScriptedModel:
    def __init__(self, name: str, outcome: object, calls: list[str]):
        self.name, self.outcome, self.calls = name, outcome, calls

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> Any:
        self.calls.append(self.name)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def with_structured_output(self, schema: type[BaseModel]) -> "ScriptedModel":
        return self


def candidate(name: str) -> ModelCandidate:
    return ModelCandidate(
        provider=ModelProvider.GROQ, model=name, temperature=0.2,
        timeout_seconds=10, max_retries=0,
    )


def routed(*models: ScriptedModel) -> LLM:
    return LLM(AgentRole.VALIDATOR, [
        RoutedModel(candidate(model.name), model) for model in models  # type: ignore[arg-type]
    ])


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "expected"),
    [("  Who\t represents me?\r\n", "Who represents me?"), ("x", "x")],
)
def test_preflight_normalizes_valid_input(query: str, expected: str) -> None:
    assert preflight_input(InputValidationRequest(original_query=query)) == expected


@pytest.mark.unit
@pytest.mark.parametrize(("query", "code"), [(" \t\n", "empty_query"), ("x" * 2001, "query_too_long")])
def test_preflight_rejects_invalid_input(query: str, code: str) -> None:
    with pytest.raises(InputValidationError) as raised:
        preflight_input(InputValidationRequest(original_query=query))
    assert raised.value.code == code


@pytest.mark.unit
@pytest.mark.regression
def test_example_config_is_a_complete_loadable_template(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    source = Path("config/environments/example.toml")
    (config_dir / "development.toml").write_text(source.read_text(), encoding="utf-8")

    settings = load_application_config(
        "development", config_dir=config_dir, env_file=tmp_path / ".env",
        environ={"OPENAI_API_KEY": "placeholder"},
    )

    assert set(settings.routes) == set(AgentRole)
    assert all(settings.routes[role] for role in AgentRole)

    with pytest.raises(ValueError, match="Gradio credentials are required"):
        load_application_config(
            "development", config_dir=config_dir, env_file=tmp_path / ".env",
            environ={"OPENAI_API_KEY": "placeholder", "REQUIRE_AUTHENTICATION": "1"},
        )


@pytest.mark.unit
@pytest.mark.regression
def test_transient_llm_failure_uses_fallback_in_order() -> None:
    calls: list[str] = []
    llm = routed(
        ScriptedModel("primary", TimeoutError(), calls),
        ScriptedModel("fallback", AIMessage(content="ok"), calls),
    )
    assert asyncio.run(llm.invoke([])).content == "ok"
    assert calls == ["primary", "fallback"]


@pytest.mark.unit
def test_non_transient_llm_failure_stops_fallback() -> None:
    calls: list[str] = []
    llm = routed(
        ScriptedModel("primary", ValueError("bad request"), calls),
        ScriptedModel("fallback", AIMessage(content="unused"), calls),
    )
    with pytest.raises(LLMError, match="Model invocation failed"):
        asyncio.run(llm.invoke([]))
    assert calls == ["primary"]


@pytest.mark.unit
@pytest.mark.security
def test_fallback_logs_do_not_leak_provider_errors(caplog) -> None:
    secret = "sensitive provider message"
    llm = routed(
        ScriptedModel("primary", TimeoutError(secret), []),
        ScriptedModel("fallback", AIMessage(content="ok"), []),
    )
    with caplog.at_level("INFO"):
        asyncio.run(llm.invoke([]))
    assert secret not in caplog.text


@pytest.mark.unit
def test_structured_validation_failure_uses_fallback() -> None:
    calls: list[str] = []
    llm = routed(
        ScriptedModel("primary", {"wrong": "shape"}, calls),
        ScriptedModel("fallback", Result(value="ok"), calls),
    )
    assert asyncio.run(llm.invoke_structured([], Result)) == Result(value="ok")
    assert calls == ["primary", "fallback"]
