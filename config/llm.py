from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.roles import AgentRole


class ModelProvider(StrEnum):
    GROQ = "groq"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class ModelCandidate(BaseModel):
    provider: ModelProvider
    model: str
    temperature: float = Field(ge=0, le=2)
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0, le=10)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("model")
    @classmethod
    def nonblank_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must not be blank")
        return value

    @model_validator(mode="after")
    def provider_limits(self) -> "ModelCandidate":
        if self.provider is ModelProvider.GROQ and self.temperature > 1:
            raise ValueError("Groq temperature must be between 0 and 1")
        return self


ModelRoute = tuple[ModelCandidate, ...]
Routes = Mapping[AgentRole, ModelRoute]
