from app.features.input_validation.config import MAX_QUERY_LENGTH
from app.features.input_validation.normalize import normalize_query
from app.features.input_validation.schemas import InputValidationRequest


def preflight_input(request: InputValidationRequest) -> str:
    """Normalize and bound input without authorizing it."""
    return normalize_query(request.original_query, MAX_QUERY_LENGTH)
