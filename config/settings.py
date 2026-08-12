import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.roles import AgentRole
from config.database import (
    DEFAULT_SQLITE_DATABASE_PATH,
    resolve_database_path,
)
from config.llm import ModelCandidate, ModelProvider, Routes

Environment = Literal["development", "test", "production"]
ROOT = Path(__file__).resolve().parents[1]
ENV_DIR = ROOT / "env"
CONFIG_DIR = ROOT / "config" / "environments"


class Settings(BaseSettings):
    environment: Environment
    debug: bool = False
    require_authentication: bool = False
    gradio_username: SecretStr | None = None
    gradio_password: SecretStr | None = None
    sqlite_database_path: Path = DEFAULT_SQLITE_DATABASE_PATH
    conversation_ttl_minutes: int = Field(default=120, ge=1, le=10_080)
    conversation_turn_retention: int = Field(default=8, ge=1, le=50)
    conversation_summarizer_raw_turn_count: int = Field(default=6, ge=0, le=50)
    diversified_research_query_count: int = Field(default=4, ge=3, le=5)
    research_results_per_query: int = Field(default=5, ge=1, le=5)
    research_max_acquisition_rounds: int = Field(default=2, ge=0, le=3)
    tavily_timeout_seconds: float = Field(default=20, gt=0, le=60)
    tavily_api_key: SecretStr | None = None
    evidence_embedding_model_id: str = Field(
        default="BAAI/bge-small-en-v1.5", min_length=1
    )
    evidence_embedding_model_revision: str = Field(
        default="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a", min_length=1
    )
    evidence_embedding_batch_size: int = Field(default=32, ge=1, le=256)
    evidence_embedding_device: str = Field(default="cpu", min_length=1)
    evidence_lexical_candidate_count: int = Field(default=20, ge=1)
    evidence_semantic_candidate_count: int = Field(default=20, ge=1)
    evidence_rrf_k: int = Field(default=60, ge=1)
    evidence_rrf_lexical_weight: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    evidence_rrf_semantic_weight: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    evidence_coverage_candidate_count: int = Field(default=12, ge=1)
    evidence_coverage_context_max: int = Field(default=16, ge=1)
    evidence_max_chunks_per_document: int = Field(default=2, ge=1)
    routes: Routes
    provider_credentials: Mapping[ModelProvider, SecretStr]

    model_config = SettingsConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def require_retrieval_modality(self) -> "Settings":
        if not (self.evidence_rrf_lexical_weight or self.evidence_rrf_semantic_weight):
            raise ValueError("At least one evidence retrieval weight must be positive")
        credentials = (self.gradio_username, self.gradio_password)
        if any(credentials) and not all(credentials):
            raise ValueError("Gradio username and password must be configured together")
        if self.require_authentication and not all(credentials):
            raise ValueError(
                "Gradio credentials are required when authentication is enabled"
            )
        return self


def load_application_config(
    environment: str | None = None,
    *,
    env_dir: Path = ENV_DIR,
    config_dir: Path = CONFIG_DIR,
    env_file: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Settings:
    process = dict(os.environ if environ is None else environ)
    selected = environment or process.get("APP_ENV", "development")
    if selected not in {"development", "test", "production"}:
        raise ValueError(f"Invalid APP_ENV: {selected}")
    dotenv_path = env_file or env_dir / f".env.{selected}"
    file_values = {k: v for k, v in dotenv_values(dotenv_path).items() if v}
    values = {**file_values, **process}
    config_path = config_dir / f"{selected}.toml"
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Invalid LLM config {config_path}: {exc}") from exc
    if set(raw) != {"api_keys", "routes"} or not all(
        isinstance(raw[name], dict) for name in raw
    ):
        raise ValueError("LLM config requires only api_keys and routes")
    try:
        route_keys = {AgentRole(name) for name in raw["routes"]}
        key_map = {
            ModelProvider(name): value for name, value in raw["api_keys"].items()
        }
    except (AttributeError, ValueError) as exc:
        raise ValueError("Unknown role or provider in LLM config") from exc
    if route_keys != set(AgentRole):
        raise ValueError("Every agent role must have an LLM route")
    routes: Routes = {}
    for role in AgentRole:
        route = raw["routes"][role.value]
        if not isinstance(route, list) or not route:
            raise ValueError(f"Empty LLM route for {role.value}")
        routes[role] = tuple(ModelCandidate.model_validate(item) for item in route)
    active = {item.provider for route in routes.values() for item in route}
    credentials: dict[ModelProvider, SecretStr] = {}
    for provider in active:
        variable = key_map.get(provider)
        secret = values.get(variable, "") if isinstance(variable, str) else ""
        if not secret.strip():
            raise ValueError(f"Missing credential for active provider {provider}")
        credentials[provider] = SecretStr(secret)
    tavily_key = str(values.get("TAVILY_API_KEY", "")).strip()
    return Settings(
        environment=selected,
        debug=values.get("DEBUG", False),
        require_authentication=values.get("REQUIRE_AUTHENTICATION", False),
        gradio_username=(
            SecretStr(values["GRADIO_USERNAME"])
            if values.get("GRADIO_USERNAME")
            else None
        ),
        gradio_password=(
            SecretStr(values["GRADIO_PASSWORD"])
            if values.get("GRADIO_PASSWORD")
            else None
        ),
        sqlite_database_path=resolve_database_path(
            values.get("SQLITE_DATABASE_PATH", "data/civic_assess.sqlite3")
        ),
        conversation_ttl_minutes=values.get("CONVERSATION_TTL_MINUTES", 120),
        conversation_turn_retention=values.get("CONVERSATION_TURN_RETENTION", 8),
        conversation_summarizer_raw_turn_count=values.get(
            "CONVERSATION_SUMMARIZER_RAW_TURN_COUNT",
            6,
        ),
        diversified_research_query_count=values.get(
            "DIVERSIFIED_RESEARCH_QUERY_COUNT",
            4,
        ),
        research_results_per_query=values.get(
            "RESEARCH_RESULTS_PER_QUERY",
            5,
        ),
        research_max_acquisition_rounds=values.get(
            "RESEARCH_MAX_ACQUISITION_ROUNDS",
            2,
        ),
        tavily_timeout_seconds=values.get("TAVILY_TIMEOUT_SECONDS", 20),
        tavily_api_key=SecretStr(tavily_key) if tavily_key else None,
        evidence_embedding_model_id=values.get(
            "EVIDENCE_EMBEDDING_MODEL_ID", "BAAI/bge-small-en-v1.5"
        ),
        evidence_embedding_model_revision=values.get(
            "EVIDENCE_EMBEDDING_MODEL_REVISION",
            "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        ),
        evidence_embedding_batch_size=values.get("EVIDENCE_EMBEDDING_BATCH_SIZE", 32),
        evidence_embedding_device=values.get("EVIDENCE_EMBEDDING_DEVICE", "cpu"),
        evidence_lexical_candidate_count=values.get(
            "EVIDENCE_LEXICAL_CANDIDATE_COUNT", 20
        ),
        evidence_semantic_candidate_count=values.get(
            "EVIDENCE_SEMANTIC_CANDIDATE_COUNT", 20
        ),
        evidence_rrf_k=values.get("EVIDENCE_RRF_K", 60),
        evidence_rrf_lexical_weight=values.get("EVIDENCE_RRF_LEXICAL_WEIGHT", 1.0),
        evidence_rrf_semantic_weight=values.get("EVIDENCE_RRF_SEMANTIC_WEIGHT", 1.0),
        evidence_coverage_candidate_count=values.get(
            "EVIDENCE_COVERAGE_CANDIDATE_COUNT", 12
        ),
        evidence_coverage_context_max=values.get("EVIDENCE_COVERAGE_CONTEXT_MAX", 16),
        evidence_max_chunks_per_document=values.get(
            "EVIDENCE_MAX_CHUNKS_PER_DOCUMENT", 2
        ),
        routes=routes,
        provider_credentials=credentials,
    )
