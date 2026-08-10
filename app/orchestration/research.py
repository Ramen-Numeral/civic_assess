import logging
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.acquisition import QueryAcquisitionFailure
from app.domain.research import ResearchPlan, ResearchQuerySet
from app.domain.validation import NonBlankText
from app.features.evidence_coverage.schemas import (
    EvidenceCoverageAssessment,
    EvidenceCoverageRequest,
    EvidenceGap,
)
from app.features.evidence_coverage.service import EvidenceCoverageService
from app.features.evidence_ingestion.errors import EvidenceIngestionError
from app.features.evidence_ingestion.service import EvidenceIngestionService
from app.features.evidence_retrieval.schemas import (
    EvidenceCandidate,
    EvidenceRetrievalSet,
)
from app.features.evidence_retrieval.service import EvidenceRetrievalService
from app.features.query_diversification.schemas import (
    GapDirectedQueryPlanningRequest,
    QueryDiversificationRequest,
)
from app.features.query_diversification.service import QueryDiversificationService
from app.features.research_acquisition.service import ResearchAcquisitionService


LOGGER = logging.getLogger(__name__)


class ResearchRound(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    round_number: int = Field(ge=0)
    query_set: ResearchQuerySet
    retrieval: EvidenceRetrievalSet
    evidence_frontier: tuple[EvidenceCandidate, ...]
    coverage_view: tuple[EvidenceCandidate, ...]
    coverage: EvidenceCoverageAssessment

    @model_validator(mode="after")
    def require_grounding_in_frontier(self) -> "ResearchRound":
        frontier = {item.chunk_id for item in self.evidence_frontier}
        available = {item.chunk_id for item in self.coverage_view}
        if not available <= frontier:
            raise ValueError("Round coverage view must belong to its frontier")
        if any(
            chunk_id not in available
            for finding in self.coverage.findings
            for chunk_id in finding.supporting_chunk_ids
        ):
            raise ValueError("Round coverage must cite its coverage view")
        return self


class ResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: ResearchPlan
    rounds: tuple[ResearchRound, ...] = Field(min_length=1)
    selected_round: ResearchRound

    @model_validator(mode="after")
    def require_selected_completed_round(self) -> "ResearchResult":
        if self.selected_round not in self.rounds:
            raise ValueError("Selected research round must be completed")
        if [item.round_number for item in self.rounds] != list(
            range(len(self.rounds))
        ):
            raise ValueError("Research rounds must be contiguous from zero")
        return self


class ResearchCoordinator:
    def __init__(
        self,
        retrieval: EvidenceRetrievalService,
        coverage: EvidenceCoverageService,
        planner: QueryDiversificationService,
        acquisition: ResearchAcquisitionService | None,
        ingestion: EvidenceIngestionService | None,
        *,
        max_acquisition_rounds: int = 2,
        coverage_context_max: int = 16,
    ) -> None:
        if not 0 <= max_acquisition_rounds <= 3:
            raise ValueError("max_acquisition_rounds must be between 0 and 3")
        if (acquisition is None) != (ingestion is None):
            raise ValueError("Acquisition and ingestion must be configured together")
        if coverage_context_max < 1:
            raise ValueError("Coverage context limit must be positive")
        self._retrieval = retrieval
        self._coverage = coverage
        self._planner = planner
        self._acquisition = acquisition
        self._ingestion = ingestion
        self._max_rounds = max_acquisition_rounds
        self._coverage_max = coverage_context_max

    async def research(
        self,
        *,
        conversation_id: UUID,
        canonical_query: NonBlankText,
    ) -> ResearchResult:
        plan = await self._planner.plan_initial(QueryDiversificationRequest(
            validated_query=canonical_query,
        ))
        query_set = plan.query_set
        first = await self._assess(
            conversation_id, plan, query_set, None, round_number=0,
        )
        rounds = [first]
        acquisition_service = self._acquisition
        ingestion_service = self._ingestion
        if acquisition_service is None or ingestion_service is None:
            return ResearchResult(
                plan=plan,
                rounds=tuple(rounds),
                selected_round=first,
            )
        for round_number in range(1, self._max_rounds + 1):
            if rounds[-1].coverage.sufficient:
                return ResearchResult(
                    plan=plan,
                    rounds=tuple(rounds),
                    selected_round=rounds[-1],
                )
            if round_number == 1:
                query_set = _planned_gap_queries(plan, rounds[-1].coverage.gaps)
            else:
                query_set = await self._planner.plan_for_gaps(
                    GapDirectedQueryPlanningRequest(
                        original_query=plan.query_set.original_query,
                        requirements=plan.requirements,
                        gaps=rounds[-1].coverage.gaps,
                    )
                )
            acquisition = await acquisition_service.acquire(query_set)
            failures = tuple(
                outcome
                for outcome in acquisition.outcomes
                if isinstance(outcome, QueryAcquisitionFailure)
            )
            if failures:
                LOGGER.warning(
                    "Research acquisition partially failed",
                    extra={
                        "event": "research.acquisition.partial_failure",
                        "round_number": round_number,
                        "failure_count": len(failures),
                        "error_codes": [item.error_code.value for item in failures],
                        "retryable_count": sum(item.retryable for item in failures),
                    },
                )
            try:
                await ingestion_service.ingest(
                    conversation_id=conversation_id,
                    query_set=query_set,
                    acquisition=acquisition,
                    round_number=round_number,
                )
            except EvidenceIngestionError as exc:
                if exc.code != "no_extractable_evidence":
                    raise
            completed = await self._assess(
                conversation_id,
                plan,
                query_set,
                rounds[-1],
                round_number=round_number,
            )
            rounds.append(completed)
        if rounds[-1].coverage.sufficient:
            selected = rounds[-1]
        else:
            selected = max(rounds, key=_round_quality)
        return ResearchResult(
            plan=plan,
            rounds=tuple(rounds),
            selected_round=selected,
        )

    async def _assess(
        self,
        conversation_id: UUID,
        plan: ResearchPlan,
        query_set: ResearchQuerySet,
        previous_round: ResearchRound | None,
        *,
        round_number: int,
    ) -> ResearchRound:
        retrieval = await self._retrieval.retrieve(
            conversation_id=conversation_id,
            query_set=query_set,
        )
        frontier = _merge_frontier(
            tuple(item.evidence for item in retrieval.ranked_candidates),
            previous_round.evidence_frontier if previous_round else (),
        )
        view = _coverage_view(
            retrieval, previous_round, frontier, maximum=self._coverage_max,
        )
        request = EvidenceCoverageRequest(
            canonical_query=plan.query_set.original_query.text,
            requirements=plan.requirements,
            evidence_view=view,
        )
        coverage = await self._coverage.assess(request)
        return ResearchRound(
            round_number=round_number,
            query_set=query_set,
            retrieval=retrieval,
            evidence_frontier=frontier,
            coverage_view=view,
            coverage=coverage,
        )


def _coverage_view(
    retrieval: EvidenceRetrievalSet,
    previous_round: ResearchRound | None,
    frontier: tuple[EvidenceCandidate, ...],
    *,
    maximum: int,
) -> tuple[EvidenceCandidate, ...]:
    candidates = {item.chunk_id: item for item in frontier}
    cited = ()
    if previous_round:
        cited = tuple(dict.fromkeys(
            chunk_id
            for finding in previous_round.coverage.findings
            for chunk_id in finding.supporting_chunk_ids
        ))
    current = tuple(
        item.evidence.chunk_id for item in retrieval.ranked_candidates
    )
    previous = previous_round.evidence_frontier if previous_round else ()
    selected = set(tuple(dict.fromkeys((
        *cited,
        *current,
        *(item.chunk_id for item in previous),
    )))[:maximum])
    return tuple(
        candidates[chunk_id]
        for chunk_id in dict.fromkeys((*current, *cited, *(
            item.chunk_id for item in previous
        )))
        if chunk_id in selected
    )


def _merge_frontier(
    current: tuple[EvidenceCandidate, ...],
    previous: tuple[EvidenceCandidate, ...],
) -> tuple[EvidenceCandidate, ...]:
    frontier: dict[UUID, EvidenceCandidate] = {}
    for candidate in (*current, *previous):
        frontier.setdefault(candidate.chunk_id, candidate)
    return tuple(frontier.values())


def _planned_gap_queries(
    plan: ResearchPlan,
    gaps: tuple[EvidenceGap, ...],
) -> ResearchQuerySet:
    incomplete = {gap.requirement_id for gap in gaps}
    selected = tuple(
        query
        for query in plan.query_set.diversified_queries
        if incomplete.intersection(query.requirement_ids)
    )
    return ResearchQuerySet(
        original_query=plan.query_set.original_query,
        diversified_queries=selected,
    )


def _round_quality(round_: ResearchRound) -> tuple[bool, int, int, int]:
    cited = {
        chunk_id
        for finding in round_.coverage.findings
        for chunk_id in finding.supporting_chunk_ids
    }
    return (
        round_.coverage.sufficient,
        -len(round_.coverage.gaps),
        len(cited),
        round_.round_number,
    )
