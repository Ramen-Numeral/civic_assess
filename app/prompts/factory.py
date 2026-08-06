from pathlib import Path

from app.prompts.base import Prompt
from app.roles import AgentRole


PROMPT_DIR = Path(__file__).parent / "templates"
Prompts = dict[AgentRole, Prompt]


def build_prompt(role: AgentRole) -> Prompt:
    return Prompt(
        name=role.value,
        description=f"Prompt for the {role.value.replace('_', ' ')} agent.",
        template_path=PROMPT_DIR / f"{role.value}.md",
    )


def build_prompts() -> Prompts:
    return {role: build_prompt(role) for role in AgentRole}
