import json
import logging
import re
from collections.abc import Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from app.domain.conversation import (
    CONVERSATION_CONTEXT_CHARACTER_LIMIT,
    ContextSourceKind,
    ConversationContext,
    ConversationContextStatus,
    ConversationStateSnapshot,
    ConversationTurn,
)
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

NUMBER = re.compile(r"(?<!\w)\$?\d[\d,.]*(?:%|st|nd|rd|th)?(?!\w)")
URL_OR_MARKDOWN_LINK = re.compile(r"https?://|www\.|\[[^\]]+\]\([^)]+\)", re.IGNORECASE)
LOGGER = logging.getLogger(__name__)
RESOLUTION_REPAIR_ATTEMPTS = 2
CLARIFICATION_FALLBACK = (
    "Could you restate your question with the person, policy, event, or claim you mean?"
)


class QueryResolutionService:
    def __init__(self, *, llm: LLMClient, prompt: Prompt) -> None:
        self._llm = llm
        self._prompt = prompt

    async def resolve(
        self,
        request: QueryResolutionRequest,
    ) -> QueryResolutionResult:
        turns = self._bounded_turns(request.context)
        remaining = CONVERSATION_CONTEXT_CHARACTER_LIMIT - sum(
            len(turn.content) for turn in turns
        )
        state_payload, state_evidence_text = _state_payload(
            request.context.state,
            remaining,
        )
        messages = [
            SystemMessage(content=self._prompt.build()),
            HumanMessage(
                content=json.dumps(
                    {
                        "normalized_query": request.normalized_query,
                        "context_status": request.context.status.value,
                        "conversation_state": state_payload,
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
        for attempt in range(RESOLUTION_REPAIR_ATTEMPTS):
            try:
                result = await self._llm.invoke_structured(
                    messages,
                    QueryResolutionResult,
                )
            except LLMError as exc:
                if exc.failures and all(
                    failure.kind is FailureKind.INVALID_OUTPUT
                    for failure in exc.failures
                ):
                    raise InvalidQueryResolutionError(
                        "Resolver returned invalid structured output"
                    ) from exc
                raise self._unavailable() from exc

            if result.resolved_query == request.normalized_query:
                result = result.model_copy(update={"context_evidence": ()})
            LOGGER.info(
                "Query resolution candidate produced",
                extra={
                    "event": "query_resolution.candidate",
                    "attempt": attempt + 1,
                    "input_query": request.normalized_query,
                    "resolved_query": result.resolved_query,
                    "clarification_question": result.clarification_question,
                    "context_evidence_count": len(result.context_evidence),
                },
            )
            try:
                self._validate_result(
                    request.normalized_query,
                    turns,
                    request.context.state,
                    state_evidence_text,
                    result,
                )
            except ValueError as exc:
                if attempt + 1 == RESOLUTION_REPAIR_ATTEMPTS:
                    LOGGER.warning(
                        "Query resolution degraded to clarification",
                        extra={
                            "event": "query_resolution.clarification_fallback",
                            "invariant": str(exc),
                        },
                    )
                    return QueryResolutionResult(
                        clarification_question=CLARIFICATION_FALLBACK,
                    )
                LOGGER.warning(
                    "Query resolution failed validation; retrying",
                    extra={
                        "event": "query_resolution.contract_repair",
                        "invariant": str(exc),
                    },
                )
                messages.append(
                    SystemMessage(
                        content=(
                            "Your previous response violated this output invariant: "
                            f"{exc}. Return a corrected result using only the supplied "
                            "query and context. Evidence references must use the supplied "
                            "source IDs."
                        )
                    )
                )
                continue
            return result
        raise AssertionError("Query resolution attempts exhausted")

    @staticmethod
    def _bounded_turns(
        context: ConversationContext,
    ) -> tuple[ConversationTurn, ...]:
        turns = context.recent_turns
        total = sum(len(turn.content) for turn in turns)
        if total <= CONVERSATION_CONTEXT_CHARACTER_LIMIT:
            return turns
        if context.status is ConversationContextStatus.COMPLETE:
            raise InvalidQueryResolutionError(
                "Complete conversation context exceeds resolver budget"
            )
        selected: list[ConversationTurn] = []
        characters = 0
        for turn in reversed(turns):
            if characters + len(turn.content) > CONVERSATION_CONTEXT_CHARACTER_LIMIT:
                break
            selected.append(turn)
            characters += len(turn.content)
        selected.reverse()
        return tuple(selected)

    @staticmethod
    def _validate_result(
        normalized_query: str,
        turns: Sequence[ConversationTurn],
        state: ConversationStateSnapshot | None,
        state_evidence_text: Sequence[str],
        result: QueryResolutionResult,
    ) -> None:
        turns_by_id = {turn.turn_id: turn for turn in turns}
        evidence_text: list[str] = []
        for evidence in result.context_evidence:
            if evidence.source_kind is ContextSourceKind.RAW_TURN:
                source = turns_by_id.get(evidence.source_id)
                valid = source is not None
                if source is not None:
                    evidence_text.append(source.content)
            else:
                valid = state is not None and evidence.source_id == state.state_id
                if valid:
                    evidence_text.extend(state_evidence_text)
            if not valid:
                raise ValueError("Resolution source ID is not in supplied context")

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
        if result.clarification_question is not None and URL_OR_MARKDOWN_LINK.search(
            result.clarification_question
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


def _state_payload(
    state: ConversationStateSnapshot | None,
    character_budget: int,
) -> tuple[dict[str, object] | None, tuple[str, ...]]:
    if state is None:
        return None, ()
    remaining = max(character_budget, 0)
    included: list[str] = []

    def include(value: str | None) -> str | None:
        nonlocal remaining
        if value is None or len(value) > remaining:
            return None
        remaining -= len(value)
        included.append(value)
        return value

    def include_many(values: Sequence[str]) -> list[str]:
        return [selected for value in values if (selected := include(value))]

    payload: dict[str, object] = {
        "state_id": str(state.state_id),
        "summary_through_sequence": state.summary_through_sequence,
        "revision": state.revision,
        "summarizer_version": state.summarizer_version,
        "important_corrections": include_many(state.important_corrections),
        "current_goal": include(state.current_goal),
        "confirmed_decisions": include_many(state.confirmed_decisions),
        "active_constraints": include_many(state.active_constraints),
        "open_questions": include_many(state.open_questions),
        "rejected_proposals": include_many(state.rejected_proposals),
        "superseded_decisions": include_many(state.superseded_decisions),
    }
    summary = state.summary[:remaining]
    if summary:
        included.append(summary)
    payload["summary"] = summary
    return payload, tuple(included)
