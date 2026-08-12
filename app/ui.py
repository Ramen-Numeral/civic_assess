import asyncio
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Literal, TypedDict
from uuid import UUID, uuid4

import gradio as gr
from gradio.themes.utils import fonts

from app.application import ChatInteractionRequest
from app.bootstrap import Application, build_application
from app.observability.llm_usage import LLMAttemptMetrics
from app.observability.progress import ProgressEvent
from config.settings import Settings, load_application_config


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


TraceEvent = ProgressEvent | LLMAttemptMetrics
TraceSink = asyncio.Queue[TraceEvent]
ACTIVE_TRACE: ContextVar[TraceSink | None] = ContextVar(
    "active_ui_trace",
    default=None,
)


class SessionProgressReporter:
    """Routes safe progress and usage events to the active Gradio request."""

    @contextmanager
    def capture(self, sink: TraceSink) -> Iterator[None]:
        token: Token[TraceSink | None] = ACTIVE_TRACE.set(sink)
        try:
            yield
        finally:
            ACTIVE_TRACE.reset(token)

    async def emit(self, event: ProgressEvent) -> None:
        sink = ACTIVE_TRACE.get()
        if sink is not None:
            sink.put_nowait(event)

    def record(self, attempt: LLMAttemptMetrics) -> None:
        sink = ACTIVE_TRACE.get()
        if sink is not None:
            sink.put_nowait(attempt)


@dataclass
class TraceStep:
    label: str
    status: str
    duration_ms: float | None = None
    detail: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


TRACE_LABELS = {
    "query_resolver": "Resolve context",
    "validator": "Validate request",
    "rewriter": "Reframe request",
    "query_diversifier": "Research evidence",
    "answer_writer": "Draft answer",
}
CALL_LABELS = {
    "query_resolver": "Query resolver",
    "validator": "Request validator",
    "rewriter": "Query reframer",
    "query_diversifier": "Research planner",
    "evidence_coverage": "Evidence assessor",
    "answer_writer": "Answer writer",
    "answer_auditor": "Answer auditor",
    "conversation_summarizer": "Conversation summarizer",
}
WORKING_LABELS = {
    "query_resolver": "Resolving conversation context",
    "validator": "Validating request",
    "rewriter": "Reframing request",
    "query_diversifier": "Researching and assessing evidence",
    "answer_writer": "Drafting and auditing answer",
}

MASTHEAD = "# Civic Assess\n\nResearch political events and public policy."


def _theme() -> gr.Theme:
    return gr.themes.Soft(
        primary_hue="blue",
        neutral_hue="slate",
        font=(
            fonts.Font("Arial"),
            fonts.Font("Helvetica"),
            fonts.Font("sans-serif"),
        ),
    )


def build_ui(
    application: Application,
    reporter: SessionProgressReporter,
    *,
    concurrency_limit: int = 2,
) -> gr.Blocks:
    if concurrency_limit < 1:
        raise ValueError("UI concurrency limit must be positive")
    def capture_message(value: str) -> tuple[str, str]:
        return value, ""

    async def respond(
        message: str,
        history: list[ChatMessage] | None,
        conversation_id: str | None,
    ) -> AsyncIterator[tuple[list[ChatMessage], str | None, str]]:
        query = message.strip()
        current = list(history or [])
        if not query:
            yield list(current), conversation_id, _render_trace([])
            return

        if conversation_id is None:
            conversation = await application.chat_interactions.create_conversation()
            conversation_id = str(conversation.conversation_id)

        current.append({"role": "user", "content": query})
        activity = "Preparing request"
        pulse = 0
        current.append({"role": "assistant", "content": _working_content(activity, pulse)})
        steps: list[TraceStep] = []
        active_step: TraceStep | None = None
        yield list(current), conversation_id, _render_trace([])

        sink: TraceSink = asyncio.Queue()
        with reporter.capture(sink):
            task = asyncio.create_task(
                application.chat_interactions.interact(
                    ChatInteractionRequest(
                        conversation_id=UUID(conversation_id),
                        client_message_id=uuid4(),
                        message=query,
                    )
                )
            )
            try:
                while not task.done() or not sink.empty():
                    try:
                        event = await asyncio.wait_for(sink.get(), timeout=0.45)
                    except TimeoutError:
                        pulse += 1
                        current[-1] = {
                            "role": "assistant",
                            "content": _working_content(activity, pulse),
                        }
                        yield list(current), conversation_id, _render_trace(
                            (*steps, active_step) if active_step else steps
                        )
                        continue
                    if isinstance(event, LLMAttemptMetrics):
                        if event.role.value == "evidence_coverage":
                            steps.append(TraceStep("Local evidence search", "completed"))
                        steps.append(_call_step(event))
                    else:
                        activity = WORKING_LABELS.get(event.phase.value, activity)
                        active_step = _phase_step(event)
                        if event.status.value == "completed":
                            active_step = None
                        elif event.status.value == "failed":
                            steps.append(active_step)
                            active_step = None
                    pulse += 1
                    current[-1] = {
                        "role": "assistant",
                        "content": _working_content(activity, pulse),
                    }
                    yield list(current), conversation_id, _render_trace(
                        (*steps, active_step) if active_step else steps
                    )
                result = await task
            except Exception as exc:
                current[-1] = {"role": "assistant", "content": _safe_error(exc)}
                yield list(current), conversation_id, _render_trace(steps)
                return
            finally:
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

        response = result.response_text or "No response was produced. Please try again."
        current[-1] = {"role": "assistant", "content": response}
        yield list(current), conversation_id, _render_trace(steps)

    with gr.Blocks(fill_height=True, title="Civic Assess") as interface:
        conversation_state = gr.State(value=None)
        submitted_message = gr.State(value="")

        with gr.Sidebar(
            label="Process Trace",
            open=True,
            width=360,
            position="right",
            elem_id="trace-sidebar",
        ):
            trace = gr.Markdown(_render_trace([]), show_label=False)

        gr.Markdown(MASTHEAD)
        chatbot = gr.Chatbot(
            height=560,
            placeholder="What would you like to know?",
            show_label=False,
            layout="bubble",
            buttons=[],
            feedback_options=[],
            elem_id="chat-panel",
        )
        with gr.Row(equal_height=True):
            message = gr.Textbox(
                placeholder="Write a civic or political question",
                show_label=False,
                lines=1,
                max_lines=6,
                scale=9,
                interactive=True,
            )
            submit = gr.Button("Submit", variant="primary", scale=1)

        inputs = [submitted_message, chatbot, conversation_state]
        outputs = [chatbot, conversation_state, trace]
        message_submit = message.submit(
            capture_message,
            message,
            [submitted_message, message],
            queue=False,
            show_progress="hidden",
        )
        message_submit.then(
            respond,
            inputs,
            outputs,
            show_progress="minimal",
            concurrency_limit=concurrency_limit,
            concurrency_id="research",
        )
        button_submit = submit.click(
            capture_message,
            message,
            [submitted_message, message],
            queue=False,
            show_progress="hidden",
        )
        button_submit.then(
            respond,
            inputs,
            outputs,
            show_progress="minimal",
            concurrency_limit=concurrency_limit,
            concurrency_id="research",
        )
    return interface


def _phase_step(event: ProgressEvent) -> TraceStep:
    phase = event.phase.value
    duration = event.details.get("duration_ms")
    return TraceStep(
        label=TRACE_LABELS.get(phase, phase.replace("_", " ").title()),
        status=event.status.value,
        duration_ms=float(duration) if isinstance(duration, (int, float)) else None,
    )


def _call_step(attempt: LLMAttemptMetrics) -> TraceStep:
    return TraceStep(
        label=CALL_LABELS.get(
            attempt.role.value,
            attempt.role.value.replace("_", " ").title(),
        ),
        status="completed" if attempt.outcome == "success" else "failed",
        duration_ms=attempt.duration_ms,
        detail=f"{attempt.provider} · {attempt.model}",
        input_tokens=attempt.input_tokens,
        output_tokens=attempt.output_tokens,
        total_tokens=attempt.total_tokens,
    )


def _render_trace(steps: Iterable[TraceStep]) -> str:
    items = list(steps)
    if not items:
        return "## Process Trace\n\nNo operations yet."
    rows = ["## Process Trace"]
    statuses = {
        "waiting": "Waiting",
        "started": "In progress",
        "completed": "Complete",
        "failed": "Failed",
    }
    for index, step in enumerate(items, 1):
        status = step.status if step.status in statuses else "waiting"
        duration = f" · {step.duration_ms:.0f} ms" if step.duration_ms is not None else ""
        rows.extend((f"**{index}. {step.label}**", f"{statuses[status]}{duration}"))
        if step.detail:
            rows.append(step.detail)
        if step.total_tokens is not None:
            rows.append(
                f"{step.input_tokens or 0:,} input · {step.output_tokens or 0:,} output"
                f" · {step.total_tokens:,} total"
            )
        rows.append("---")
    token_total = sum(step.total_tokens or 0 for step in items)
    if token_total:
        rows.append(f"**Total · {token_total:,} tokens**")
    return "\n\n".join(rows)


def _safe_error(exc: Exception) -> str:
    message = getattr(exc, "safe_message", None)
    return message if isinstance(message, str) else (
        "The request could not be completed. Please try again."
    )


def _working_content(activity: str, pulse: int) -> str:
    return f"_{activity}{'.' * (pulse % 3 + 1)}_"


def main() -> None:
    settings = load_application_config()
    reporter = SessionProgressReporter()
    application = build_application(
        settings,
        progress_reporter=reporter,
        llm_usage_reporter=reporter,
    )
    build_ui(
        application,
        reporter,
        concurrency_limit=3 if settings.require_authentication else 2,
    ).queue(max_size=20, api_open=False).launch(
        theme=_theme(),
        auth=_authentication(settings),
    )


def _authentication(settings: Settings) -> tuple[str, str] | None:
    if not settings.require_authentication:
        return None
    assert settings.gradio_username is not None
    assert settings.gradio_password is not None
    return (
        settings.gradio_username.get_secret_value(),
        settings.gradio_password.get_secret_value(),
    )


if __name__ == "__main__":
    main()
