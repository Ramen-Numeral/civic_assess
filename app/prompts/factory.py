from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from app.features.query_reframe.schemas import QueryReframeMode
from app.prompts.base import Prompt
from app.roles import AgentRole


PROMPT_DIR = Path(__file__).parent / "templates"
Prompts = dict[AgentRole, Prompt]
QueryReframePrompts = Mapping[QueryReframeMode, Prompt]


def build_prompt(role: AgentRole) -> Prompt:
    return Prompt(
        name=role.value,
        description=f"Prompt for the {role.value.replace('_', ' ')} agent.",
        template_path=PROMPT_DIR / f"{role.value}.md",
    )


def build_prompts() -> Prompts:
    return {role: build_prompt(role) for role in AgentRole}


def build_query_reframe_prompt(mode: QueryReframeMode) -> Prompt:
    return Prompt(
        name=f"query_reframe_{mode.value}",
        description=f"Prompt for {mode.value.replace('_', ' ')}.",
        template_path=PROMPT_DIR / f"query_reframe_{mode.value}.md",
    )


def build_query_reframe_prompts() -> QueryReframePrompts:
    return MappingProxyType({
        mode: build_query_reframe_prompt(mode)
        for mode in QueryReframeMode
    })
