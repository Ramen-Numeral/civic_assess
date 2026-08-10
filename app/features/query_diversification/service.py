import json
import re
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from app.domain.research import (
    DiversifiedResearchQuery,
    OriginalResearchQuery,
    ResearchQuerySet,
)
from app.features.query_diversification.errors import (
    InvalidQueryDiversificationError,
    QueryDiversificationError,
)
from app.features.query_diversification.schemas import (
    GapDirectedQueryPlanningRequest,
    QueryDiversificationProposal,
    QueryDiversificationRequest,
)
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.errors import FailureKind, LLMError
from app.prompts.base import Prompt


NUMBER = re.compile(r"(?<!\w)\$?\d[\d,.]*(?:%|st|nd|rd|th)?(?!\w)")
URL_OR_MARKDOWN_LINK = re.compile(r"https?://|www\.|\[[^\]]+\]\([^)]+\)", re.I)


class QueryDiversificationService:
    def __init__(
        self,
        *,
        llm: LLMClient,
        prompt: Prompt,
        gap_prompt: Prompt,
        query_count: int,
    ) -> None:
        if not 3 <= query_count <= 5:
            raise ValueError("query_count must be between 3 and 5")
        self._llm = llm
        self._prompt = prompt
        self._gap_prompt = gap_prompt
        self._query_count = query_count

    async def diversify(
        self,
        request: QueryDiversificationRequest,
    ) -> ResearchQuerySet:
        messages = [
            SystemMessage(
                content=self._prompt.build(
                    diversified_query_count=self._query_count,
                )
            ),
            HumanMessage(
                content=json.dumps(
                    {"validated_query": request.validated_query},
                    ensure_ascii=False,
                )
            ),
        ]
        proposal = await self._propose(messages)
        self._validate(
            request.validated_query,
            proposal,
            minimum=1,
            allowed_text=request.validated_query,
        )
        return self._query_set(request.validated_query, proposal)

    async def plan_for_gaps(
        self,
        request: GapDirectedQueryPlanningRequest,
    ) -> ResearchQuerySet:
        messages = [
            SystemMessage(content=self._gap_prompt.build(
                diversified_query_count=self._query_count,
            )),
            HumanMessage(content=json.dumps({
                "canonical_query": request.canonical_query,
                "gaps": [gap.model_dump(mode="json") for gap in request.gaps],
            }, ensure_ascii=False)),
        ]
        proposal = await self._propose(messages)
        allowed_text = " ".join([
            request.canonical_query,
            *(value for gap in request.gaps for value in (
                gap.description, gap.evidence_requirement,
            )),
        ])
        self._validate(
            request.canonical_query,
            proposal,
            minimum=0,
            allowed_text=allowed_text,
        )
        return self._query_set(request.canonical_query, proposal)

    async def _propose(self, messages) -> QueryDiversificationProposal:
        try:
            return await self._llm.invoke_structured(
                messages, QueryDiversificationProposal,
            )
        except LLMError as exc:
            if exc.failures and all(
                failure.kind is FailureKind.INVALID_OUTPUT
                for failure in exc.failures
            ):
                raise InvalidQueryDiversificationError(
                    "Diversifier returned invalid structured output"
                ) from exc
            raise QueryDiversificationError(
                "diversifier_unavailable",
                "I couldn't prepare research queries right now. Please try again.",
                retryable=True,
            ) from exc
        except ValueError as exc:
            raise InvalidQueryDiversificationError(
                "Diversifier returned invalid structured output"
            ) from exc

    def _validate(
        self,
        original_query: str,
        proposal: QueryDiversificationProposal,
        *,
        minimum: int,
        allowed_text: str,
    ) -> None:
        try:
            self._validate_proposal(
                original_query,
                proposal,
                minimum=minimum,
                allowed_text=allowed_text,
            )
        except ValueError as exc:
            raise InvalidQueryDiversificationError(
                "Diversifier output violated query invariants"
            ) from exc

    @staticmethod
    def _query_set(
        original_query: str,
        proposal: QueryDiversificationProposal,
    ) -> ResearchQuerySet:
        return ResearchQuerySet(
            original_query=OriginalResearchQuery(
                query_id=uuid4(),
                text=original_query,
            ),
            diversified_queries=tuple(
                DiversifiedResearchQuery(
                    query_id=uuid4(),
                    facet=query.facet,
                    text=query.text,
                    research_goal=query.research_goal,
                )
                for query in proposal.queries
            ),
        )

    def _validate_proposal(
        self,
        original_query: str,
        proposal: QueryDiversificationProposal,
        *,
        minimum: int,
        allowed_text: str,
    ) -> None:
        if not minimum <= len(proposal.queries) <= self._query_count:
            raise ValueError("Diversifier returned an invalid query count")

        normalized_original = _normalize(original_query)
        normalized_queries = [_normalize(query.text) for query in proposal.queries]
        normalized_goals = [
            _normalize(query.research_goal) for query in proposal.queries
        ]
        if len(set(normalized_queries)) != len(normalized_queries):
            raise ValueError("Diversified query text must be unique")
        if len(set(normalized_goals)) != len(normalized_goals):
            raise ValueError("Diversified research goals must be unique")
        if normalized_original in normalized_queries:
            raise ValueError("Diversified query must differ from the original")

        allowed_numbers = _numbers(allowed_text)
        for query in proposal.queries:
            if not _numbers(query.text) <= allowed_numbers:
                raise ValueError("Diversified query introduced an unsupported number")
            if URL_OR_MARKDOWN_LINK.search(query.text):
                raise ValueError("Diversified query must not contain links")


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def _numbers(value: str) -> set[str]:
    return {match.group().casefold() for match in NUMBER.finditer(value)}
