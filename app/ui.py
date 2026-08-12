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
from app.bootstrap.application import Application, build_application
from app.observability.progress import ProgressEvent


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


TraceSink = asyncio.Queue[ProgressEvent]
ACTIVE_TRACE: ContextVar[TraceSink | None] = ContextVar(
    "active_ui_trace",
    default=None,
)


class SessionProgressReporter:
    """Routes safe progress events to the active Gradio request only."""

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


@dataclass
class TraceStep:
    label: str
    status: str
    duration_ms: float | None = None


TRACE_LABELS = {
    "query_resolver": "Resolve context",
    "validator": "Validate request",
    "rewriter": "Reframe request",
    "query_diversifier": "Research evidence",
    "answer_writer": "Draft answer",
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
) -> gr.Blocks:
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
        steps: dict[str, TraceStep] = {}
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
                            steps.values()
                        )
                        continue
                    _record_step(steps, event)
                    activity = WORKING_LABELS.get(event.phase.value, activity)
                    pulse += 1
                    current[-1] = {
                        "role": "assistant",
                        "content": _working_content(activity, pulse),
                    }
                    yield list(current), conversation_id, _render_trace(
                        steps.values()
                    )
                result = await task
            except Exception as exc:
                current[-1] = {"role": "assistant", "content": _safe_error(exc)}
                yield list(current), conversation_id, _render_trace(steps.values())
                return
            finally:
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

        response = result.response_text or "No response was produced. Please try again."
        current[-1] = {"role": "assistant", "content": response}
        yield list(current), conversation_id, _render_trace(steps.values())

    with gr.Blocks(fill_height=True, title="Civic Assess") as interface:
        conversation_state = gr.State(value=None)

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
            buttons=["copy"],
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

        inputs = [message, chatbot, conversation_state]
        outputs = [chatbot, conversation_state, trace]
        message.submit(
            respond,
            inputs,
            outputs,
            show_progress="minimal",
            concurrency_limit=None,
        )
        submit.click(
            respond,
            inputs,
            outputs,
            show_progress="minimal",
            concurrency_limit=None,
        )
    return interface


def _record_step(steps: dict[str, TraceStep], event: ProgressEvent) -> None:
    phase = event.phase.value
    step = steps.setdefault(
        phase,
        TraceStep(
            label=TRACE_LABELS.get(phase, phase.replace("_", " ").title()),
            status=event.status.value,
        ),
    )
    step.status = event.status.value
    duration = event.details.get("duration_ms")
    step.duration_ms = float(duration) if isinstance(duration, (int, float)) else None


def _render_trace(steps: Iterable[TraceStep]) -> str:
    items = list(steps)
    if not items:
        items = [TraceStep(label=label, status="waiting") for label in TRACE_LABELS.values()]
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
        rows.extend(
            (
                f"**{index}. {step.label}**",
                f"{statuses[status]}{duration}",
                "---",
            )
        )
    return "\n\n".join(rows)


def _safe_error(exc: Exception) -> str:
    message = getattr(exc, "safe_message", None)
    return message if isinstance(message, str) else (
        "The request could not be completed. Please try again."
    )


def _working_content(activity: str, pulse: int) -> str:
    return f"_{activity}{'.' * (pulse % 3 + 1)}_"


def main() -> None:
    reporter = SessionProgressReporter()
    application = build_application(progress_reporter=reporter)
    build_ui(application, reporter).queue().launch(
        theme=_theme(),
    )


if __name__ == "__main__":
    main()
