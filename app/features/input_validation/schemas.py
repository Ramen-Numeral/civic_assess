from pydantic import BaseModel, ConfigDict, Field


class InputValidationRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    query: str
