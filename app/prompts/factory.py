from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from app.features.query_reframe.schemas import QueryReframeMode
from app.prompts.base import Prompt


PROMPT_DIR = Path(__file__).parent / "templates"
QueryReframePrompts = Mapping[QueryReframeMode, Prompt]


def build_input_validation_prompt() -> Prompt:
    return Prompt(
        name="validator",
        description="Assess request scope, behavior, and instruction integrity.",
        template_path=PROMPT_DIR / "validator.md",
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


def build_query_diversification_prompt() -> Prompt:
    return Prompt(
        name="query_diversification",
        description="Generate intent-preserving diversified research queries.",
        template_path=PROMPT_DIR / "query_diversification.md",
    )
