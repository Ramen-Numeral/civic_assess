import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.research import (
    DiversifiedResearchQuery, OriginalResearchQuery, ResearchPlan,
    ResearchQuerySet, ResearchRequirement,
)
from app.features.evidence.models import (
    EvidenceCandidate, EvidenceCoverageAssessment, EvidenceGap,
    EvidenceRetrievalSet, RankedEvidenceCandidate, RequirementFinding,
)
from app.features.query_diversification.schemas import (
    InitialResearchPlanProposal, ProposedEvidenceAngle,
    ProposedResearchRequirement, QueryDiversificationRequest,
)
from app.features.query_diversification.service import QueryDiversificationService
from app.orchestration.research import ResearchCoordinator


pytestmark = [pytest.mark.integration, pytest.mark.regression]


def fixtures():
    conversation_id, requirement_id, query_id = uuid4(), uuid4(), uuid4()
    query_set = ResearchQuerySet(
        original_query=OriginalResearchQuery(query_id=query_id, text="Canonical question"),
        diversified_queries=(DiversifiedResearchQuery(
            query_id=uuid4(), requirement_ids=(requirement_id,),
            evidence_angle="Direct evidence", text="Canonical question evidence",
        ),),
    )
    plan = ResearchPlan(
        requirements=(ResearchRequirement(
            requirement_id=requirement_id,
            description="Establish the answer.", evidence_angles=("Direct evidence",),
        ),),
        query_set=query_set,
    )
    evidence = EvidenceCandidate(
        chunk_id=uuid4(), document_id=uuid4(), text="Evidence",
        title="Source", canonical_url="https://example.com/source",
        start_offset=0, end_offset=8,
        last_discovered_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    retrieval = EvidenceRetrievalSet(
        conversation_id=conversation_id, embedding_version=None, query_results=(),
        ranked_candidates=(RankedEvidenceCandidate(
            evidence=evidence, rank=1, rrf_score=0.1,
            supporting_query_ids=(query_id,),
        ),),
    )
    return conversation_id, plan, evidence, retrieval


class Retriever:
    def __init__(self, result):
        self.result = result

    async def retrieve(self, **arguments):
        return self.result


class Coverage:
    def __init__(self, result):
        self.result = result

    async def assess(self, request):
        return self.result


class Planner:
    def __init__(self, plan):
        self.plan, self.gap_calls = plan, 0

    async def plan_initial(self, request):
        return self.plan

    async def plan_for_gaps(self, request):
        self.gap_calls += 1
        raise AssertionError("gap planning should not run")


def coordinator(retrieval, coverage, planner):
    return ResearchCoordinator(
        Retriever(retrieval), Coverage(coverage), planner,
        acquisition=None, ingestion=None, max_acquisition_rounds=2,
    )


def test_sufficient_local_evidence_stops_before_acquisition() -> None:
    conversation_id, plan, evidence, retrieval = fixtures()
    assessment = EvidenceCoverageAssessment(findings=(RequirementFinding(
        requirement_id=plan.requirements[0].requirement_id,
        statement="The answer is established.",
        supporting_chunk_ids=(evidence.chunk_id,),
        evidence_basis="observed", source_fitness="fit",
    ),))
    planner = Planner(plan)

    result = asyncio.run(coordinator(retrieval, assessment, planner).research(
        conversation_id=conversation_id, canonical_query="Canonical question",
    ))

    assert result.selected_round.round_number == 0
    assert result.selected_round.coverage.sufficient
    assert planner.gap_calls == 0


def test_missing_acquisition_returns_best_local_assessment() -> None:
    conversation_id, plan, _, retrieval = fixtures()
    requirement = plan.requirements[0]
    assessment = EvidenceCoverageAssessment(gaps=(EvidenceGap(
        requirement_id=requirement.requirement_id,
        description="Material evidence gap", missing_evidence="Direct evidence",
    ),))

    result = asyncio.run(coordinator(retrieval, assessment, Planner(plan)).research(
        conversation_id=conversation_id, canonical_query="Canonical question",
    ))

    assert result.rounds == (result.selected_round,)
    assert not result.selected_round.coverage.sufficient


def test_one_angle_plan_is_repaired_or_degrades_explicitly() -> None:
    class LLM:
        def __init__(self, *proposals):
            self.proposals = list(proposals)

        async def invoke_structured(self, messages, schema):
            return self.proposals.pop(0)

    class Prompt:
        def build(self, **values):
            return "plan"

    def proposal(*angles):
        return InitialResearchPlanProposal(
            temporal_scope={},
            requirements=(ProposedResearchRequirement(
                description="Establish documented policy positions.",
                evidence_angles=tuple(ProposedEvidenceAngle(
                    description=angle, text=f"immigration policy {angle}"
                ) for angle in angles),
            ),),
        )

    request = QueryDiversificationRequest(
        validated_query="What is the current immigration policy debate?"
    )
    repaired = QueryDiversificationService(
        llm=LLM(proposal("enforcement"), proposal("enforcement", "reform")),
        prompt=Prompt(), gap_prompt=Prompt(), query_count=4,
    )
    degraded = QueryDiversificationService(
        llm=LLM(proposal("enforcement"), proposal("enforcement")),
        prompt=Prompt(), gap_prompt=Prompt(), query_count=4,
    )

    assert len(asyncio.run(repaired.plan_initial(request)).query_set.diversified_queries) == 2
    assert len(asyncio.run(degraded.plan_initial(request)).query_set.diversified_queries) == 1


def test_evidence_gap_runs_acquisition_ingestion_and_reassessment() -> None:
    conversation_id, plan, evidence, retrieval = fixtures()
    gap = EvidenceCoverageAssessment(gaps=(EvidenceGap(
        requirement_id=plan.requirements[0].requirement_id,
        description="Missing evidence", missing_evidence="Direct evidence",
    ),))
    covered = EvidenceCoverageAssessment(findings=(RequirementFinding(
        requirement_id=plan.requirements[0].requirement_id,
        statement="The gap is resolved.", supporting_chunk_ids=(evidence.chunk_id,),
        evidence_basis="observed", source_fitness="fit",
    ),))

    class Sequence:
        def __init__(self, *values):
            self.values = list(values)

        async def retrieve(self, **arguments):
            return self.values.pop(0)

    class Assessments(Sequence):
        async def assess(self, request):
            return self.values.pop(0)

    class Acquisition:
        calls = 0

        async def acquire(self, query_set):
            self.calls += 1
            return type("AcquisitionResult", (), {"outcomes": ()})()

    class Ingestion:
        calls = 0

        async def ingest(self, **arguments):
            self.calls += 1

    acquisition, ingestion = Acquisition(), Ingestion()
    result = asyncio.run(ResearchCoordinator(
        Sequence(retrieval, retrieval), Assessments(gap, covered), Planner(plan),
        acquisition, ingestion, max_acquisition_rounds=2,
    ).research(conversation_id=conversation_id, canonical_query="Canonical question"))

    assert acquisition.calls == ingestion.calls == 1
    assert [item.round_number for item in result.rounds] == [0, 1]
    assert result.cumulative_coverage.sufficient
