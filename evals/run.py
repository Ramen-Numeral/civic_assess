import argparse
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel

from app.application import ChatInteractionRequest
from app.bootstrap import build_application
from app.features.input_validation.schemas import InputValidationRequest
from app.features.input_validation.service import InputValidationService
from app.features.query_reframe.schemas import QueryReframeRequest
from app.features.query_reframe.service import (
    QueryReframeService,
    determine_reframe_mode,
)
from app.infrastructure.llm.factory import build_llms
from app.observability.context import run_context
from app.observability.llm_usage import LLMAttemptMetrics, LLMUsageReporter
from app.prompts.factory import (
    build_input_validation_prompt,
    build_query_reframe_prompts,
)
from app.roles import AgentRole
from config.settings import ENV_DIR, Settings, load_application_config
from evals.models import (
    RequestRoutingCase,
    RequestRoutingRun,
    RubricRun,
    RubricScenario,
    ScenarioTurn,
)
from evals.reporting import write_request_routing_report, write_rubric_report

ROOT = Path(__file__).parent
Model = TypeVar("Model", bound=BaseModel)


class UsageRecorder(LLMUsageReporter):
    def __init__(self) -> None:
        self.attempts: list[LLMAttemptMetrics] = []

    def record(self, attempt: LLMAttemptMetrics) -> None:
        self.attempts.append(attempt)

    def for_run(self, run_id: str) -> tuple[LLMAttemptMetrics, ...]:
        return tuple(item for item in self.attempts if item.run_id == run_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run live behavioral evaluations")
    parser.add_argument("suite", choices=("routing", "scenarios"))
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--case", action="append", dest="cases", help="Scenario case ID; repeatable"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "reports")
    args = parser.parse_args()
    _require_runs(args.runs)
    if args.suite == "routing":
        if args.cases:
            parser.error("--case is available only for scenarios")
        asyncio.run(_routing(args.runs, args.output))
    else:
        asyncio.run(_scenarios(args.runs, args.output, args.cases))


async def _routing(repetitions: int, output: Path) -> None:
    cases = _load(ROOT / "cases" / "request_routing.json", RequestRoutingCase)
    settings, usage = _settings(), UsageRecorder()
    llms = build_llms(settings, usage)
    validator = InputValidationService(
        llm=llms[AgentRole.VALIDATOR], prompt=build_input_validation_prompt()
    )
    reframer = QueryReframeService(
        llm=llms[AgentRole.REWRITER], prompts=build_query_reframe_prompts()
    )
    runs = []
    for case in cases:
        for number in range(1, repetitions + 1):
            run_id, started = str(uuid4()), perf_counter()
            with run_context(run_id):
                result = await validator.validate(
                    InputValidationRequest(
                        original_query=case.query,
                    )
                )
                proposal = None
                if result.disposition.value == "reframe":
                    proposal = await reframer.reframe(
                        QueryReframeRequest(
                            original_query=case.query,
                            resolved_query=result.normalized_query,
                            analysis=result.analysis,
                            mode=determine_reframe_mode(result.analysis),
                        )
                    )
            runs.append(
                RequestRoutingRun(
                    case,
                    result,
                    number,
                    _elapsed(started),
                    usage.for_run(run_id),
                    proposal,
                )
            )
    write_request_routing_report(tuple(runs), output)
    print(f"Wrote {len(runs)} routing results to {output / 'request_routing.md'}")


async def _scenarios(
    repetitions: int,
    output: Path,
    case_ids: list[str] | None,
) -> None:
    cases = _load(ROOT / "cases" / "rubric_scenarios.json", RubricScenario)
    if case_ids:
        selected = set(case_ids)
        cases = tuple(case for case in cases if case.case_id in selected)
        missing = selected - {case.case_id for case in cases}
        if missing:
            raise ValueError(f"Unknown scenarios: {', '.join(sorted(missing))}")
    settings, usage, runs = _settings(), UsageRecorder(), []
    if settings.tavily_api_key is None:
        raise RuntimeError("TAVILY_API_KEY is required for scenario evaluations")
    with TemporaryDirectory(prefix="civic-evals-") as directory:
        settings = settings.model_copy(
            update={
                "sqlite_database_path": Path(directory) / "evals.sqlite3",
            }
        )
        application = build_application(settings, llm_usage_reporter=usage)
        for case in cases:
            for number in range(1, repetitions + 1):
                conversation = await application.chat_interactions.create_conversation()
                run_id, started, turns = str(uuid4()), perf_counter(), []
                with run_context(run_id):
                    for query, expectation in zip(case.turns, case.expectations):
                        turn_started = perf_counter()
                        try:
                            result = await application.chat_interactions.interact(
                                ChatInteractionRequest(
                                    conversation_id=conversation.conversation_id,
                                    client_message_id=uuid4(),
                                    message=query,
                                )
                            )
                            turns.append(
                                ScenarioTurn(
                                    query, expectation, result, _elapsed(turn_started)
                                )
                            )
                        except Exception as exc:
                            turns.append(
                                ScenarioTurn(
                                    query,
                                    expectation,
                                    None,
                                    _elapsed(turn_started),
                                    type(exc).__name__,
                                    str(exc),
                                    getattr(exc, "workflow_state", None),
                                )
                            )
                runs.append(
                    RubricRun(
                        case,
                        tuple(turns),
                        number,
                        _elapsed(started),
                        usage.for_run(run_id),
                    )
                )
    write_rubric_report(tuple(runs), output)
    print(f"Wrote {len(runs)} scenario results to {output / 'rubric_scenarios.md'}")


def _load(path: Path, schema: type[Model]) -> tuple[Model, ...]:
    return tuple(schema.model_validate(item) for item in json.loads(path.read_text()))


def _settings() -> Settings:
    environment = os.environ.get("APP_ENV", "development")
    return load_application_config(
        environment,
        env_file=ENV_DIR / f".env.{environment}",
        environ={},
    )


def _require_runs(value: int) -> None:
    if value < 1:
        raise ValueError("runs must be positive")


def _elapsed(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


if __name__ == "__main__":
    main()
