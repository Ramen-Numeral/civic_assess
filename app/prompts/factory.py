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


def build_gap_query_planning_prompt() -> Prompt:
    return Prompt(
        name="gap_query_planning",
        description="Translate evidence gaps into bounded research queries.",
        template_path=PROMPT_DIR / "gap_query_planning.md",
    )


def build_evidence_coverage_prompt() -> Prompt:
    return Prompt(
        name="evidence_coverage",
        description="Assess whether retrieved evidence sufficiently covers a query.",
        template_path=PROMPT_DIR / "evidence_coverage.md",
    )


def build_answer_synthesis_prompt() -> Prompt:
    return Prompt(
        name="answer_synthesis",
        description="Draft atomic claims from grounded evidence.",
        template_path=PROMPT_DIR / "answer_synthesis.md",
    )


def build_answer_audit_prompt() -> Prompt:
    return Prompt(
        name="answer_audit",
        description="Audit answer support, completeness, and perspective coverage.",
        template_path=PROMPT_DIR / "answer_audit.md",
    )


def build_conversation_state_prompt() -> Prompt:
    return Prompt(
        name="conversation_state",
        description="Compress conversation history into structured state.",
        template_path=PROMPT_DIR / "conversation_state.md",
    )
