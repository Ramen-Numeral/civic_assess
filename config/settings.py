import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field, SecretStr
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
    sqlite_database_path: Path = DEFAULT_SQLITE_DATABASE_PATH
    conversation_ttl_minutes: int = Field(default=120, ge=1, le=10_080)
    conversation_turn_retention: int = Field(default=8, ge=1, le=50)
    diversified_research_query_count: int = Field(default=4, ge=3, le=5)
    max_research_rounds: int = Field(default=2, ge=1, le=5)
    routes: Routes
    provider_credentials: Mapping[ModelProvider, SecretStr]

    model_config = SettingsConfigDict(extra="forbid", frozen=True)


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
        key_map = {ModelProvider(name): value for name, value in raw["api_keys"].items()}
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
    return Settings(
        environment=selected,
        debug=values.get("DEBUG", False),
        sqlite_database_path=resolve_database_path(
            values.get("SQLITE_DATABASE_PATH", "data/civic_assess.sqlite3")
        ),
        conversation_ttl_minutes=values.get("CONVERSATION_TTL_MINUTES", 120),
        conversation_turn_retention=values.get("CONVERSATION_TURN_RETENTION", 8),
        diversified_research_query_count=values.get(
            "DIVERSIFIED_RESEARCH_QUERY_COUNT",
            4,
        ),
        max_research_rounds=values.get("MAX_RESEARCH_ROUNDS", 2),
        routes=routes,
        provider_credentials=credentials,
    )
