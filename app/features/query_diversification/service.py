import json
import re
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from app.domain.research import (
    DiversifiedResearchQuery,
    OriginalResearchQuery,
    ResearchPlan,
    ResearchRequirement,
    ResearchQuerySet,
)
from app.features.query_diversification.errors import (
    InvalidQueryDiversificationError,
    QueryDiversificationError,
)
from app.features.query_diversification.schemas import (
    GapQueryPlanningProposal,
    GapDirectedQueryPlanningRequest,
    InitialResearchPlanProposal,
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

    async def plan_initial(
        self,
        request: QueryDiversificationRequest,
    ) -> ResearchPlan:
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
        proposal = await self._propose(messages, InitialResearchPlanProposal)
        self._validate_plan(proposal, request.validated_query)
        queries = tuple(
            angle
            for requirement in proposal.requirements
            for angle in requirement.evidence_angles
        )
        self._validate_queries(
            request.validated_query, queries, request.validated_query, minimum=1,
        )
        if len(queries) > self._query_count:
            raise InvalidQueryDiversificationError(
                "Research plan exceeded the evidence-angle budget"
            )
        requirements = []
        diversified = []
        for proposed in proposal.requirements:
            requirement_id = uuid4()
            angles = []
            for angle in proposed.evidence_angles:
                angles.append(angle.description)
                diversified.append(DiversifiedResearchQuery(
                    query_id=uuid4(),
                    requirement_ids=(requirement_id,),
                    evidence_angle=angle.description,
                    text=angle.text,
                ))
            requirements.append(ResearchRequirement(
                requirement_id=requirement_id,
                description=proposed.description,
                evidence_angles=tuple(angles),
            ))
        original_query = OriginalResearchQuery(
            query_id=uuid4(), text=request.validated_query,
        )
        return ResearchPlan(
            requirements=tuple(requirements),
            query_set=ResearchQuerySet(
                original_query=original_query,
                diversified_queries=tuple(diversified),
            ),
        )

    async def plan_for_gaps(
        self,
        request: GapDirectedQueryPlanningRequest,
    ) -> ResearchQuerySet:
        requirements = {
            requirement.requirement_id: requirement
            for requirement in request.requirements
        }
        gaps = {
            f"G{position}": gap
            for position, gap in enumerate(request.gaps, 1)
        }
        messages = [
            SystemMessage(content=self._gap_prompt.build(
                diversified_query_count=self._query_count,
            )),
            HumanMessage(content=json.dumps({
                "canonical_query": request.original_query.text,
                "gaps": [{
                    "ref": ref,
                    "requirement": requirements[gap.requirement_id].description,
                    "investigated_angles": list(
                        requirements[gap.requirement_id].evidence_angles
                    ),
                    "description": gap.description,
                    "missing_evidence": gap.missing_evidence,
                } for ref, gap in gaps.items()],
            }, ensure_ascii=False)),
        ]
        proposal = await self._propose(messages, GapQueryPlanningProposal)
        allowed_text = " ".join([
            request.original_query.text,
            *(requirement.description for requirement in request.requirements),
            *(
                angle
                for requirement in request.requirements
                for angle in requirement.evidence_angles
            ),
            *(value for gap in request.gaps for value in (
                gap.description, gap.missing_evidence,
            )),
        ])
        self._validate_queries(
            request.original_query.text,
            proposal.queries,
            allowed_text,
            minimum=0,
        )
        diversified = []
        for query in proposal.queries:
            if any(ref not in gaps for ref in query.gap_refs):
                raise InvalidQueryDiversificationError(
                    "Gap query targets an unknown gap"
                )
            targeted = tuple(gaps[ref] for ref in query.gap_refs)
            diversified.append(DiversifiedResearchQuery(
                query_id=uuid4(),
                requirement_ids=tuple(dict.fromkeys(
                    gap.requirement_id for gap in targeted
                )),
                text=query.text,
            ))
        return ResearchQuerySet(
            original_query=request.original_query,
            diversified_queries=tuple(diversified),
        )

    async def _propose(self, messages, schema):
        try:
            return await self._llm.invoke_structured(messages, schema)
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

    def _validate_queries(
        self,
        original_query: str,
        queries,
        allowed_text: str,
        *,
        minimum: int,
    ) -> None:
        try:
            self._validate_proposal(original_query, queries, minimum, allowed_text)
        except ValueError as exc:
            raise InvalidQueryDiversificationError(
                "Diversifier output violated query invariants"
            ) from exc

    @staticmethod
    def _validate_plan(proposal, allowed_text: str) -> None:
        allowed_numbers = _numbers(allowed_text)
        requirements = [
            _normalize(requirement.description)
            for requirement in proposal.requirements
        ]
        if len(requirements) != len(set(requirements)):
            raise InvalidQueryDiversificationError(
                "Research requirements must be distinct"
            )
        for requirement in proposal.requirements:
            descriptions = [
                _normalize(angle.description)
                for angle in requirement.evidence_angles
            ]
            if len(descriptions) != len(set(descriptions)):
                raise InvalidQueryDiversificationError(
                    "Evidence angles must be distinct within a requirement"
                )
            if any(
                not _numbers(value) <= allowed_numbers
                for value in (requirement.description, *descriptions)
            ):
                raise InvalidQueryDiversificationError(
                    "Research plan introduced an unsupported number"
                )

    def _validate_proposal(
        self,
        original_query: str,
        queries,
        minimum: int,
        allowed_text: str,
    ) -> None:
        if not minimum <= len(queries) <= self._query_count:
            raise ValueError("Diversifier returned an invalid query count")

        normalized_original = _normalize(original_query)
        normalized_queries = [_normalize(query.text) for query in queries]
        if len(set(normalized_queries)) != len(normalized_queries):
            raise ValueError("Diversified query text must be unique")
        if normalized_original in normalized_queries:
            raise ValueError("Diversified query must differ from the original")

        allowed_numbers = _numbers(allowed_text)
        for query in queries:
            if not _numbers(query.text) <= allowed_numbers:
                raise ValueError("Diversified query introduced an unsupported number")
            if URL_OR_MARKDOWN_LINK.search(query.text):
                raise ValueError("Diversified query must not contain links")


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def _numbers(value: str) -> set[str]:
    return {match.group().casefold() for match in NUMBER.finditer(value)}
