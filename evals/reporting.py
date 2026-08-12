from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from statistics import median

from app.domain.validation import Disposition, InputGateAnalysis, InputGateResult
from app.features.answer_synthesis.renderer import cited_sources
from app.observability.llm_usage import LLMAttemptMetrics, TokenSource
from evals.models import RequestRoutingRun, RubricRun, ScenarioTurn


def write_request_routing_report(
    runs: tuple[RequestRoutingRun, ...],
    output: Path,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    grouped = _group_routing_runs(runs)
    labels = [item.value for item in Disposition]
    confusion = {expected: Counter() for expected in labels}
    axis_hits: Counter[str] = Counter()
    rows, stable, correct = [], 0, 0

    for case_runs in grouped.values():
        expected = _expected(case_runs[0])
        outcomes = Counter(tuple(_observed(run.result).items()) for run in case_runs)
        modal_items, modal_count = outcomes.most_common(1)[0]
        modal = dict(modal_items)
        confusion[expected["disposition"]][modal["disposition"]] += 1
        stable += modal_count == len(case_runs)
        correct += modal["disposition"] == expected["disposition"]
        for key in expected:
            axis_hits[key] += sum(
                _observed(run.result)[key] == expected[key] for run in case_runs
            )
        proposal = next(
            (
                run.reframe_proposal.proposed_query
                for run in case_runs
                if run.reframe_proposal is not None
            ),
            None,
        )
        rows.append((case_runs[0], expected, modal, modal_count, proposal))

    case_count, run_count = len(rows), len(runs)
    repetitions = run_count // max(case_count, 1)
    lines = [
        "# Prompt-gating evaluation",
        "",
        "Live labeled cases measure scope, behavioral safety, instruction integrity, "
        "and the resulting application route.",
        "",
        "## Result",
        "",
        f"- **Disposition accuracy:** {correct}/{case_count} ({_ratio(correct, case_count)})",
        (
            f"- **Repeatable cases:** {stable}/{case_count} ({_ratio(stable, case_count)})"
            if repetitions > 1
            else "- **Repeatability:** not measured (one run per case)"
        ),
        f"- **Runs:** {run_count} across {case_count} cases",
        "",
        "## Decision matrix",
        "",
        "Rows are expected routes; columns are observed modal routes.",
        "",
        "| Expected \\ Observed | " + " | ".join(labels) + " |",
        "|---|" + "---:|" * len(labels),
    ]
    lines.extend(
        f"| **{expected}** | "
        + " | ".join(str(confusion[expected][actual]) for actual in labels)
        + " |"
        for expected in labels
    )
    lines += [
        "",
        "## Agreement by decision axis",
        "",
        "| Axis | Correct calls | Agreement |",
        "|---|---:|---:|",
        *(
            f"| {key.replace('_', ' ').title()} | {axis_hits[key]}/{run_count} | "
            f"{_ratio(axis_hits[key], run_count)} |"
            for key in ("scope", "behavior", "integrity", "disposition")
        ),
        "",
        "## Case results",
        "",
        "| Case | Expected | Observed | Agreement |",
        "|---|---|---|---:|",
        *(
            f"| {run.case.case_id} | {_route(expected)} | {_route(modal)} | "
            f"{modal_count}/{len(grouped[run.case.case_id])} |"
            for run, expected, modal, modal_count, _ in rows
        ),
    ]
    differences = [
        row
        for row in rows
        if row[1] != row[2] or row[3] != len(grouped[row[0].case.case_id])
    ]
    lines += ["", "## Classification differences", ""]
    if differences:
        for run, expected, modal, modal_count, _ in differences:
            lines += [
                f"- **{run.case.case_id}:** expected `{_route(expected)}`; observed "
                f"`{_route(modal)}` in {modal_count}/{len(grouped[run.case.case_id])} runs."
            ]
    else:
        lines.append("None observed.")
    reframes = [row for row in rows if row[4]]
    if reframes:
        lines += [
            "",
            "## Proposed reframes",
            "",
            "| Original request | Proposed request |",
            "|---|---|",
            *(
                f"| {_cell(run.case.query)} | {_cell(proposal or '')} |"
                for run, _, _, _, proposal in reframes
            ),
        ]
    lines += [
        "",
        "## Runtime",
        "",
        *_usage_lines(_usage(runs), [run.wall_clock_ms for run in runs]),
    ]
    (output / "request_routing.md").write_text("\n".join(lines) + "\n")


def write_rubric_report(runs: tuple[RubricRun, ...], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    evaluated = [
        (run, [(turn, _evaluate_turn(turn)) for turn in run.turns]) for run in runs
    ]
    passed = sum(
        all(all(result for _, result in checks) for _, checks in turns)
        for _, turns in evaluated
    )
    lines = [
        "# Scenario Evaluation",
        "",
        "The application is exercised end to end against explicit behavioral and "
        "evidence expectations. These are live model results, not unit tests.",
        "",
        "## Result",
        "",
        f"- **Passing runs:** {passed}/{len(runs)}",
        f"- **Scenarios represented:** {len({run.scenario.case_id for run in runs})}",
        "",
        "| Scenario | Checks satisfied |",
        "|---|---:|",
        *(
            f"| {run.scenario.title} | "
            f"{sum(result for _, checks in turns for _, result in checks)}/"
            f"{sum(len(checks) for _, checks in turns)} |"
            for run, turns in evaluated
        ),
    ]
    for run, turns in evaluated:
        lines += [
            "",
            f"## {run.scenario.title}",
            "",
            f"Run {run.run_number} · {run.wall_clock_ms / 1000:.1f}s total",
        ]
        for index, (turn, checks) in enumerate(turns, 1):
            state = _state(turn)
            research, answer = state.get("research_result"), state.get("answer_result")
            quality = (
                answer.final_audit.answer_quality
                if answer and answer.final_audit
                else None
            )
            sources = (
                len(cited_sources(answer.draft, research.cumulative_evidence))
                if answer and research
                else 0
            )
            gaps = len(research.cumulative_coverage.gaps) if research else 0
            angles = (
                sum(len(item.evidence_angles) for item in research.plan.requirements)
                if research
                else 0
            )
            lines += [
                "",
                f"### Turn {index}",
                "",
                f"**Question:** {turn.query}",
                "",
                "| Check | Result |",
                "|---|---|",
                *(
                    f"| {_cell(label)} | {'PASS' if result else 'FAIL'} |"
                    for label, result in checks
                ),
                "",
                f"**Observed:** route `{_turn_route(turn)}` · {sources} cited sources · "
                f"answer quality {quality if quality is not None else 'n/a'}/5 · "
                f"{gaps} unresolved gaps · {angles} evidence angles · "
                f"{turn.wall_clock_ms / 1000:.1f}s",
                "",
                "### Response",
                "",
                turn.result.response_text
                if turn.result and turn.result.response_text
                else "No response was produced.",
            ]
            if turn.error_code:
                lines += ["", f"**Error:** `{turn.error_code}` — {turn.error_message}"]
    lines += [
        "",
        "## Runtime",
        "",
        *_usage_lines(_usage(runs), [run.wall_clock_ms for run in runs]),
    ]
    (output / "rubric_scenarios.md").write_text("\n".join(lines) + "\n")


def _evaluate_turn(turn: ScenarioTurn) -> list[tuple[str, bool]]:
    expected, state = turn.expectation, _state(turn)
    research, answer = state.get("research_result"), state.get("answer_result")
    checks = [(f"Route is `{expected.route}`", _turn_route(turn) == expected.route)]
    if expected.minimum_sources:
        count = (
            len(cited_sources(answer.draft, research.cumulative_evidence))
            if answer and research
            else 0
        )
        checks.append(
            (
                f"At least {expected.minimum_sources} cited sources ({count})",
                count >= expected.minimum_sources,
            )
        )
    if expected.minimum_answer_quality:
        quality = (
            answer.final_audit.answer_quality if answer and answer.final_audit else 0
        )
        checks.append(
            (
                f"Answer audit at least {expected.minimum_answer_quality}/5 ({quality}/5)",
                quality >= expected.minimum_answer_quality,
            )
        )
    if expected.maximum_unresolved_gaps is not None:
        gaps = len(research.cumulative_coverage.gaps) if research else 0
        checks.append(
            (
                f"At most {expected.maximum_unresolved_gaps} unresolved gaps ({gaps})",
                gaps <= expected.maximum_unresolved_gaps,
            )
        )
    if expected.minimum_evidence_angles:
        angles = (
            sum(len(item.evidence_angles) for item in research.plan.requirements)
            if research
            else 0
        )
        checks.append(
            (
                f"At least {expected.minimum_evidence_angles} evidence angles ({angles})",
                angles >= expected.minimum_evidence_angles,
            )
        )
    return checks


def _turn_route(turn: ScenarioTurn) -> str:
    if turn.error_code:
        return "error"
    state = _state(turn)
    if state.get("answer_result"):
        return "answer"
    if route := state.get("chat_route"):
        return route.value
    if gate := state.get("gate_result"):
        return gate.disposition.value
    return "unknown"


def _state(turn: ScenarioTurn):
    return turn.result.workflow_state if turn.result else turn.workflow_state or {}


def _group_routing_runs(
    runs: tuple[RequestRoutingRun, ...],
) -> dict[str, list[RequestRoutingRun]]:
    grouped: dict[str, list[RequestRoutingRun]] = defaultdict(list)
    for run in runs:
        grouped[run.case.case_id].append(run)
    return grouped


def _expected(run: RequestRoutingRun) -> dict[str, str]:
    return {
        **_analysis(run.case.expected),
        "disposition": run.case.expected_disposition.value,
    }


def _observed(result: InputGateResult) -> dict[str, str]:
    return {**_analysis(result.analysis), "disposition": result.disposition.value}


def _analysis(value: InputGateAnalysis) -> dict[str, str]:
    behavior = value.behavior.value
    if behavior.startswith("requires_"):
        behavior = "requires_reframe"
    elif behavior == "allowed":
        behavior = "safe_intent"
    return {
        "scope": value.scope.value,
        "behavior": behavior,
        "integrity": value.instruction_integrity.value,
    }


def _route(value: dict[str, str]) -> str:
    return (
        f"{value['scope']} / {value['behavior']} / {value['integrity']} "
        f"→ {value['disposition']}"
    )


def _usage_lines(
    attempts: tuple[LLMAttemptMetrics, ...],
    times: list[float],
) -> list[str]:
    reported = [
        item for item in attempts if item.token_source is TokenSource.PROVIDER_REPORTED
    ]
    total_tokens = sum(item.total_tokens or 0 for item in reported)
    failures = sum(item.outcome != "success" for item in attempts)
    fallbacks = sum(item.fallback_triggered for item in attempts)
    return [
        f"- Wall-clock: {sum(times) / 1000:.1f}s total · {median(times or [0]) / 1000:.1f}s median",
        f"- LLM attempts: {len(attempts)} · failures: {failures} · fallbacks: {fallbacks}",
        f"- Provider-reported tokens: {total_tokens} ({len(reported)}/{len(attempts)} attempts reported usage)",
    ]


def _usage(
    runs: Iterable[RequestRoutingRun | RubricRun],
) -> tuple[LLMAttemptMetrics, ...]:
    return tuple(attempt for run in runs for attempt in run.usage)


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator / max(denominator, 1):.1%}"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
