import re
import unicodedata

from app.features.input_validation.errors import InputValidationError
from app.features.input_validation.schemas import InputValidationRequest

MAX_QUERY_LENGTH = 2000
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def normalize_query(query: str, max_length: int) -> str:
    query = unicodedata.normalize("NFKC", query)
    query = query.replace("\r\n", "\n").replace("\r", "\n")
    query = CONTROL_CHARS.sub("", query)
    query = re.sub(r"[ \t]+", " ", query)
    query = re.sub(r"\n{3,}", "\n\n", query).strip()
    if not query:
        raise InputValidationError("empty_query", "Please enter a question.")
    if len(query) > max_length:
        raise InputValidationError(
            "query_too_long",
            f"Please shorten your question to {max_length} characters or fewer.",
        )
    return query


def preflight_input(request: InputValidationRequest) -> str:
    """Normalize and bound input without authorizing it."""
    return normalize_query(request.original_query, MAX_QUERY_LENGTH)
