from pydantic import BaseModel, ConfigDict


class InputValidationRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    original_query: str
    resolved_query: str | None = None
