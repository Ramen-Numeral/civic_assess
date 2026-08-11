import json
import logging
import re
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from app.domain.research import (
    DiversifiedResearchQuery,
    OriginalResearchQuery,
    ResearchPlan,
    ResearchRequirement,
    ResearchQuerySet,
    TemporalScope,
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
LOGGER = logging.getLogger(__name__)


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
        scope = TemporalScope()
        for attempt in range(2):
            try:
                proposal = await self._propose(messages, InitialResearchPlanProposal)
                scope = self._verbatim_scope(proposal, request.validated_query)
                self._validate_plan(proposal, request.validated_query)
                if not attempt:
                    self._require_decomposition(proposal, request.validated_query)
                break
            except (InvalidQueryDiversificationError, QueryDiversificationError) as exc:
                if attempt:
                    LOGGER.warning(
                        "Initial research plan degraded to direct evidence",
                        extra={"event": "research.plan.direct_fallback"},
                    )
                    return self._direct_evidence_plan(request.validated_query, scope)
                LOGGER.warning(
                    "Initial research plan failed validation; retrying",
                    extra={"event": "research.plan.contract_repair"},
                )
                messages = [*messages, HumanMessage(content=json.dumps({
                    "validation_feedback": (
                        f"{exc}. Correct the plan without adding facts, numbers, "
                        "links, or scope absent from the validated query."
                    ),
                }))]
        priority = sorted(
            range(len(proposal.requirements)),
            key=lambda index: -len(proposal.requirements[index].evidence_angles),
        )
        positions = [0] * len(proposal.requirements)
        selected = []
        while len(selected) < self._query_count:
            before = len(selected)
            for index in priority:
                angles = proposal.requirements[index].evidence_angles
                if positions[index] < len(angles):
                    selected.append((index, angles[positions[index]]))
                    positions[index] += 1
                    if len(selected) == self._query_count:
                        break
            if len(selected) == before:
                break
        selected = [
            (index, angle.model_copy(
                update={"text": _with_spans(angle.text, scope.spans)},
            ))
            for index, angle in selected
        ]
        queries = tuple(angle for _, angle in selected)
        try:
            self._validate_queries(
                request.validated_query, queries, request.validated_query, minimum=1,
            )
        except InvalidQueryDiversificationError:
            LOGGER.warning("Initial query plan degraded to direct evidence",
                           extra={"event": "research.plan.direct_fallback"})
            return self._direct_evidence_plan(request.validated_query, scope)
        requirement_ids = tuple(uuid4() for _ in proposal.requirements)
        retained = [[] for _ in proposal.requirements]
        for index, angle in selected:
            retained[index].append(angle.description)
        requirements = tuple(
            ResearchRequirement(
                requirement_id=requirement_id,
                description=proposed.description,
                evidence_expectation=proposed.evidence_expectation,
                evidence_angles=tuple(retained[index]),
            )
            for index, (requirement_id, proposed) in enumerate(zip(
                requirement_ids, proposal.requirements, strict=True,
            ))
        )
        diversified = tuple(DiversifiedResearchQuery(
            query_id=uuid4(),
            requirement_ids=(requirement_ids[index],),
            evidence_angle=angle.description,
            text=angle.text,
        ) for index, angle in selected)
        original_query = OriginalResearchQuery(
            query_id=uuid4(), text=request.validated_query,
        )
        return ResearchPlan(
            requirements=requirements,
            temporal_scope=scope,
            query_set=ResearchQuerySet(
                original_query=original_query,
                diversified_queries=diversified,
            ),
        )

    @staticmethod
    def _direct_evidence_plan(
        query: str, temporal_scope: TemporalScope | None = None,
    ) -> ResearchPlan:
        requirement_id = uuid4()
        return ResearchPlan(
            requirements=(ResearchRequirement(
                requirement_id=requirement_id, description=query,
                evidence_expectation="Evidence directly addressing the query.",
                evidence_angles=(),
            ),),
            query_set=ResearchQuerySet(
                original_query=OriginalResearchQuery(query_id=uuid4(), text=query),
                diversified_queries=(),
            ),
            temporal_scope=temporal_scope or TemporalScope(),
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
                "temporal_scope": list(request.temporal_scope.spans),
                "gaps": [{
                    "ref": ref,
                    "requirement": requirements[gap.requirement_id].description,
                    "evidence_expectation": requirements[
                        gap.requirement_id
                    ].evidence_expectation,
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
                requirement.evidence_expectation
                for requirement in request.requirements
                if requirement.evidence_expectation is not None
            ),
            *(
                angle
                for requirement in request.requirements
                for angle in requirement.evidence_angles
            ),
            *(value for gap in request.gaps for value in (
                gap.description, gap.missing_evidence,
            )),
        ])
        queries = self._usable_gap_queries(
            proposal.queries, gaps, request.original_query.text, allowed_text,
            request.temporal_scope,
        )
        if not queries:
            retry = [*messages, HumanMessage(content=json.dumps({
                "validation_feedback": (
                    "No proposed query was usable. Return only valid, distinct "
                    "gap-targeted queries using the supplied gap references."
                ),
            }))]
            proposal = await self._propose(retry, GapQueryPlanningProposal)
            queries = self._usable_gap_queries(
                proposal.queries, gaps, request.original_query.text, allowed_text,
                request.temporal_scope,
            )
        diversified = []
        for text, refs in queries:
            targeted = tuple(gaps[ref] for ref in refs)
            diversified.append(DiversifiedResearchQuery(
                query_id=uuid4(),
                requirement_ids=tuple(dict.fromkeys(
                    gap.requirement_id for gap in targeted
                )),
                text=text,
            ))
        return ResearchQuerySet(
            original_query=request.original_query,
            diversified_queries=tuple(diversified),
        )

    def _usable_gap_queries(
        self, queries, gaps, original_query: str, allowed_text: str,
        temporal_scope: TemporalScope,
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        original = _normalize(original_query)
        allowed_numbers = _numbers(allowed_text)
        required_time = tuple(_normalize(span) for span in temporal_scope.spans)
        deduped: dict[str, tuple[str, tuple[str, ...]]] = {}
        for query in queries:
            key = _normalize(query.text)
            refs = tuple(dict.fromkeys(ref for ref in query.gap_refs if ref in gaps))
            if (not key or not refs or key == original
                    or URL_OR_MARKDOWN_LINK.search(query.text)
                    or not _numbers(query.text) <= allowed_numbers
                    or not all(span in key for span in required_time)):
                continue
            if key in deduped:
                text, prior = deduped[key]
                deduped[key] = (text, tuple(dict.fromkeys((*prior, *refs))))
            else:
                deduped[key] = (query.text, refs)
        candidates = list(deduped.values())
        if len(candidates) <= self._query_count:
            return tuple(candidates)
        priority = sorted(
            gaps, key=lambda ref: -sum(ref in refs for _, refs in candidates)
        )
        selected = []
        while candidates and len(selected) < self._query_count:
            for ref in priority:
                match = next((index for index, (_, refs) in enumerate(candidates)
                              if ref in refs), None)
                if match is not None:
                    selected.append(candidates.pop(match))
                    if len(selected) == self._query_count:
                        break
        return tuple(selected)

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
                f"Diversifier output violated query invariants: {exc}"
            ) from exc

    @staticmethod
    def _verbatim_scope(proposal, allowed_text: str) -> TemporalScope:
        allowed = _normalize(allowed_text)
        spans = tuple(_normalize(span) for span in proposal.temporal_scope.spans)
        if len(spans) != len(set(spans)) or not all(
            span in allowed for span in spans
        ):
            raise InvalidQueryDiversificationError(
                "Temporal scope must be copied verbatim from the validated query"
            )
        return proposal.temporal_scope

    @staticmethod
    def _require_decomposition(proposal, validated_query: str) -> None:
        if len(proposal.requirements) > 1:
            return
        description = _normalize(proposal.requirements[0].description)
        query = _normalize(validated_query)
        if description == query or description in query or query in description:
            raise InvalidQueryDiversificationError(
                "A requirement must state what to establish rather than restate "
                "the query; decompose distinct questions and materially distinct "
                "documented positions into separate requirements"
            )

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
                for value in (
                    requirement.description,
                    *descriptions,
                    *(
                        (requirement.evidence_expectation,)
                        if requirement.evidence_expectation is not None
                        else ()
                    ),
                )
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


def _with_spans(text: str, spans: tuple[str, ...]) -> str:
    normalized = _normalize(text)
    missing = [
        span for span in spans
        if _normalize(span) and _normalize(span) not in normalized
    ]
    return " ".join([text.strip(), *missing]) if missing else text


def _numbers(value: str) -> set[str]:
    return {match.group().casefold() for match in NUMBER.finditer(value)}
