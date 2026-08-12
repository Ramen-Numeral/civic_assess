import asyncio
from types import SimpleNamespace

import pytest

from app.observability.progress import ProgressEvent, ProgressStatus
from app.roles import AgentRole
from app.ui import SessionProgressReporter, _working_content, build_ui


pytestmark = pytest.mark.unit


def test_progress_is_scoped_to_the_active_ui_request() -> None:
    async def scenario() -> None:
        reporter = SessionProgressReporter()
        sink = asyncio.Queue()
        event = ProgressEvent(
            run_id="run-1",
            sequence=1,
            phase=AgentRole.VALIDATOR,
            status=ProgressStatus.STARTED,
            message="Validator started.",
        )

        await reporter.emit(event)
        assert sink.empty()

        with reporter.capture(sink):
            await reporter.emit(event)

        assert await sink.get() == event

    asyncio.run(scenario())


def test_ui_builds_chat_and_trace_sidebar() -> None:
    application = SimpleNamespace(chat_interactions=object())
    interface = build_ui(application, SessionProgressReporter())
    config = interface.get_config_file()
    element_ids = {
        component["props"].get("elem_id") for component in config["components"]
    }

    assert "chat-panel" in element_ids
    assert "trace-sidebar" in element_ids


def test_working_message_pulses_without_exposing_reasoning() -> None:
    assert [_working_content("Researching sources", index) for index in range(3)] == [
        "_Researching sources._",
        "_Researching sources.._",
        "_Researching sources..._",
    ]
