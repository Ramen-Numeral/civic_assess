from functools import cached_property
from pathlib import Path
from string import Formatter
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class Prompt(BaseModel):
    """Reusable prompt definition backed by a template file."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    name: str
    description: str
    template_path: Path

    @field_validator("template_path")
    @classmethod
    def validate_template_path(cls, value: Path) -> Path:
        path = value.resolve()

        if not path.is_file():
            raise ValueError(f"Prompt template does not exist: {path}")

        return path

    @cached_property
    def template(self) -> str:
        """Load and cache the template text."""
        return self.template_path.read_text(encoding="utf-8")

    def build(self, **values: Any) -> str:
        """Render the prompt using runtime values."""
        expected = self.placeholders()
        provided = set(values)

        missing = expected - provided
        if missing:
            raise ValueError(f"Missing prompt values: {', '.join(sorted(missing))}")

        try:
            return self.template.format(**values)
        except (KeyError, ValueError, IndexError) as exc:
            raise ValueError(f"Could not render prompt '{self.name}': {exc}") from exc

    def placeholders(self) -> set[str]:
        """Return placeholders declared in the template."""
        return {
            field_name
            for _, field_name, _, _ in Formatter().parse(self.template)
            if field_name
        }
