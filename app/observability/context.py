from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4


RUN_ID: ContextVar[str | None] = ContextVar("run_id", default=None)


def current_run_id() -> str | None:
    return RUN_ID.get()


@contextmanager
def run_context(run_id: str | None = None) -> Iterator[str]:
    resolved = run_id or current_run_id() or str(uuid4())
    token = RUN_ID.set(resolved)
    try:
        yield resolved
    finally:
        RUN_ID.reset(token)
