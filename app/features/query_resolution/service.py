import json
import re
from collections.abc import Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from app.domain.conversation import ConversationTurn
from app.features.query_resolution.errors import (
    InvalidQueryResolutionError,
    QueryResolutionError,
)
from app.features.query_resolution.schemas import (
    QueryResolutionRequest,
    QueryResolutionResult,
)
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.errors import FailureKind, LLMError
from app.prompts.base import Prompt


MAX_CONTEXT_CHARACTERS = 12_000
NUMBER = re.compile(r"(?<!\w)\$?\d[\d,.]*(?:%|st|nd|rd|th)?(?!\w)")
URL_OR_MARKDOWN_LINK = re.compile(r"https?://|www\.|\[[^\]]+\]\([^)]+\)", re.I)


class QueryResolutionService:
    def __init__(
        self,
        *,
        llm: LLMClient,
        prompt: Prompt,
        turn_retention: int,
    ) -> None:
        if turn_retention < 1:
            raise ValueError("turn_retention must be positive")
        self._llm = llm
        self._prompt = prompt
        self._turn_retention = turn_retention

    async def resolve(
        self,
        request: QueryResolutionRequest,
    ) -> QueryResolutionResult:
        turns = self._bounded_turns(request.recent_turns)
        messages = [
            SystemMessage(content=self._prompt.build()),
            HumanMessage(
                content=json.dumps(
                    {
                        "normalized_query": request.normalized_query,
                        "recent_turn_order": "oldest_to_newest",
                        "recent_turns": [
                            {
                                "turn_id": str(turn.turn_id),
                                "role": turn.role.value,
                                "content": turn.content,
                                "recency": (
                                    "most_recent"
                                    if index == len(turns) - 1
                                    else f"{len(turns) - index - 1}_turns_ago"
                                ),
                            }
                            for index, turn in enumerate(turns)
                        ],
                    },
                    ensure_ascii=False,
                )
            ),
        ]
        try:
            result = await self._llm.invoke_structured(
                messages,
                QueryResolutionResult,
            )
            self._validate_result(request.normalized_query, turns, result)
        except LLMError as exc:
            if exc.failures and all(
                failure.kind is FailureKind.INVALID_OUTPUT
                for failure in exc.failures
            ):
                raise InvalidQueryResolutionError(
                    "Resolver returned invalid structured output"
                ) from exc
            raise self._unavailable() from exc
        except ValueError as exc:
            raise InvalidQueryResolutionError(
                "Resolver output violated resolution invariants"
            ) from exc
        return result

    def _bounded_turns(
        self,
        turns: Sequence[ConversationTurn],
    ) -> tuple[ConversationTurn, ...]:
        selected: list[ConversationTurn] = []
        characters = 0
        for turn in reversed(turns[-self._turn_retention:]):
            if characters + len(turn.content) > MAX_CONTEXT_CHARACTERS:
                break
            selected.append(turn)
            characters += len(turn.content)
        selected.reverse()
        return tuple(selected)

    @staticmethod
    def _validate_result(
        normalized_query: str,
        turns: Sequence[ConversationTurn],
        result: QueryResolutionResult,
    ) -> None:
        turns_by_id = {turn.turn_id: turn for turn in turns}
        evidence_text: list[str] = []
        for evidence in result.context_evidence:
            source = turns_by_id.get(evidence.turn_id)
            if source is None or evidence.excerpt not in source.content:
                raise ValueError("Resolution evidence is not in supplied context")
            evidence_text.append(evidence.excerpt)

        if result.resolved_query is not None:
            changed = result.resolved_query != normalized_query
            if changed and not result.context_evidence:
                raise ValueError("Changed resolution requires context evidence")
            if not changed and result.context_evidence:
                raise ValueError("Unchanged resolution must not claim context evidence")

        output = result.resolved_query or result.clarification_question or ""
        allowed_numbers = _numbers(" ".join([normalized_query, *evidence_text]))
        if not _numbers(output) <= allowed_numbers:
            raise ValueError("Resolution introduced an unsupported number")
        if (
            result.clarification_question is not None
            and URL_OR_MARKDOWN_LINK.search(result.clarification_question)
        ):
            raise ValueError("Clarification must not contain links")

    @staticmethod
    def _unavailable() -> QueryResolutionError:
        return QueryResolutionError(
            "resolver_unavailable",
            "I couldn't interpret that request right now. Please try again.",
            retryable=True,
        )


def _numbers(value: str) -> set[str]:
    return {match.group().casefold() for match in NUMBER.finditer(value)}
