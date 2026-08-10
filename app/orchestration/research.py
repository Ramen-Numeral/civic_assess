import logging
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.acquisition import QueryAcquisitionFailure
from app.domain.research import OriginalResearchQuery, ResearchQuerySet
from app.domain.validation import NonBlankText
from app.features.evidence_coverage.schemas import (
    EvidenceCoverageAssessment,
    EvidenceCoverageRequest,
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
    coverage: EvidenceCoverageAssessment

    @model_validator(mode="after")
    def require_grounding_in_frontier(self) -> "ResearchRound":
        available = {item.chunk_id for item in self.evidence_frontier}
        if any(
            chunk_id not in available
            for point in self.coverage.covered_points
            for chunk_id in point.supporting_chunk_ids
        ):
            raise ValueError("Round coverage must cite its evidence frontier")
        return self


class ResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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
        acquisition: ResearchAcquisitionService,
        ingestion: EvidenceIngestionService,
        *,
        max_acquisition_rounds: int = 2,
    ) -> None:
        if not 0 <= max_acquisition_rounds <= 3:
            raise ValueError("max_acquisition_rounds must be between 0 and 3")
        self._retrieval = retrieval
        self._coverage = coverage
        self._planner = planner
        self._acquisition = acquisition
        self._ingestion = ingestion
        self._max_rounds = max_acquisition_rounds

    async def research(
        self,
        *,
        conversation_id: UUID,
        canonical_query: NonBlankText,
    ) -> ResearchResult:
        query_set = ResearchQuerySet(
            original_query=OriginalResearchQuery(
                query_id=uuid4(),
                text=canonical_query,
            ),
            diversified_queries=(),
        )
        first = await self._assess(
            conversation_id, canonical_query, query_set, (), round_number=0,
        )
        rounds = [first]
        for round_number in range(1, self._max_rounds + 1):
            if rounds[-1].coverage.sufficient:
                return ResearchResult(
                    rounds=tuple(rounds),
                    selected_round=rounds[-1],
                )
            query_set = await self._planner.plan_for_gaps(
                GapDirectedQueryPlanningRequest(
                    canonical_query=canonical_query,
                    gaps=rounds[-1].coverage.gaps,
                )
            )
            acquisition = await self._acquisition.acquire(query_set)
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
                await self._ingestion.ingest(
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
                canonical_query,
                query_set,
                rounds[-1].evidence_frontier,
                round_number=round_number,
            )
            rounds.append(completed)
        if rounds[-1].coverage.sufficient:
            selected = rounds[-1]
        else:
            selected = max(rounds, key=_round_quality)
        return ResearchResult(rounds=tuple(rounds), selected_round=selected)

    async def _assess(
        self,
        conversation_id: UUID,
        canonical_query: str,
        query_set: ResearchQuerySet,
        previous_frontier: tuple[EvidenceCandidate, ...],
        *,
        round_number: int,
    ) -> ResearchRound:
        retrieval = await self._retrieval.retrieve(
            conversation_id=conversation_id,
            query_set=query_set,
        )
        frontier = _merge_frontier(
            tuple(item.evidence for item in retrieval.ranked_candidates),
            previous_frontier,
        )
        request = EvidenceCoverageRequest(
            canonical_query=canonical_query,
            evidence_frontier=frontier,
        )
        coverage = await self._coverage.assess(request)
        return ResearchRound(
            round_number=round_number,
            query_set=query_set,
            retrieval=retrieval,
            evidence_frontier=frontier,
            coverage=coverage,
        )


def _merge_frontier(
    current: tuple[EvidenceCandidate, ...],
    previous: tuple[EvidenceCandidate, ...],
) -> tuple[EvidenceCandidate, ...]:
    frontier: dict[UUID, EvidenceCandidate] = {}
    for candidate in (*current, *previous):
        frontier.setdefault(candidate.chunk_id, candidate)
    return tuple(frontier.values())


def _round_quality(round_: ResearchRound) -> tuple[bool, int, int, int]:
    cited = {
        chunk_id
        for point in round_.coverage.covered_points
        for chunk_id in point.supporting_chunk_ids
    }
    return (
        round_.coverage.sufficient,
        -len(round_.coverage.gaps),
        len(cited),
        round_.round_number,
    )
