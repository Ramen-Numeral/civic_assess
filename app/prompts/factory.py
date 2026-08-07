from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from app.features.query_reframe.schemas import QueryReframeMode
from app.prompts.base import Prompt
from app.roles import AgentRole


PROMPT_DIR = Path(__file__).parent / "templates"
QueryReframePrompts = Mapping[QueryReframeMode, Prompt]


def build_prompt(role: AgentRole) -> Prompt:
    template_name = (
        "query_resolution"
        if role is AgentRole.QUERY_RESOLVER
        else role.value
    )
    return Prompt(
        name=role.value,
        description=f"Prompt for the {role.value.replace('_', ' ')} agent.",
        template_path=PROMPT_DIR / f"{template_name}.md",
    )


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


def build_query_resolution_prompt() -> Prompt:
    return Prompt(
        name="query_resolution",
        description="Resolve conversational references in a user query.",
        template_path=PROMPT_DIR / "query_resolution.md",
    )
