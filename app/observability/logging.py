import json
import logging
import traceback
from datetime import UTC, datetime


STANDARD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
HANDLER_NAME = "civic_assess"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({
            key: value
            for key, value in record.__dict__.items()
            if key not in STANDARD_FIELDS
        })
        if record.exc_info:
            exception_type, _, exception_traceback = record.exc_info
            payload["exception"] = {
                "type": exception_type.__name__,
                "stack": [
                    {
                        "file": frame.filename,
                        "line": frame.lineno,
                        "function": frame.name,
                    }
                    for frame in traceback.extract_tb(exception_traceback)
                ],
            }
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(*, debug: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    handler = next(
        (item for item in root.handlers if item.get_name() == HANDLER_NAME),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler()
        handler.set_name(HANDLER_NAME)
        root.addHandler(handler)
    handler.setFormatter(JsonFormatter())
