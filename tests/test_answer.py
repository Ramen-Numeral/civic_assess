import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.features.answer_synthesis.errors import InvalidAnswerProposalError
from app.features.answer_synthesis.schemas import (
    AnswerAudit, AnswerFinding, AnswerParagraph, GroundedAnswerRequest,
    NaturalAnswerDraft,
)
from app.features.evidence.models import EvidenceCandidate
from app.orchestration.answer import AnswerCoordinator


pytestmark = [pytest.mark.integration, pytest.mark.regression]


class Synthesis:
    def __init__(self, initial, audits, revised=None):
        self.initial, self.audits, self.revised = initial, list(audits), revised
        self.audit_calls = 0

    async def draft(self, request):
        return self.initial

    async def audit(self, request, draft, validation_feedback=None):
        self.audit_calls += 1
        outcome = self.audits.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def revise(self, request, draft, audit):
        return self.revised


def answer_fixture():
    evidence = EvidenceCandidate(
        chunk_id=uuid4(), document_id=uuid4(), text="Evidence passage",
        title="Source", canonical_url="https://example.com/source",
        start_offset=0, end_offset=16,
        last_discovered_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    request = GroundedAnswerRequest(
        canonical_query="What happened?", evidence=(evidence,),
        findings=(AnswerFinding(
            statement="A documented event occurred.",
            supporting_chunk_ids=(evidence.chunk_id,), evidence_basis="observed",
            source_fitness="fit",
        ),),
    )
    paragraph = AnswerParagraph(
        paragraph_id=uuid4(), text="A documented event occurred. [[E1]]",
        finding_indexes=(0,), supporting_chunk_ids=(evidence.chunk_id,),
    )
    return request, NaturalAnswerDraft(paragraphs=(paragraph,))


def audit(draft, quality: int) -> AnswerAudit:
    return AnswerAudit(
        answer_quality=quality,
        paragraph_support={draft.paragraphs[0].paragraph_id: quality},
        revision_instructions=() if quality == 5 else ("Narrow the claim.",),
        evidence_note="The source directly bears on the answer.",
    )


def test_first_attempt_requires_five_and_sources_are_links_not_passages() -> None:
    request, draft = answer_fixture()
    answer = asyncio.run(AnswerCoordinator(Synthesis(draft, [audit(draft, 5)])).answer(request))

    assert not answer.degraded and not answer.revision_failed
    assert '<a href="https://example.com/source">[1]</a>' in answer.text
    assert "https://example.com/source" in answer.text
    assert "Evidence passage" not in answer.text
    assert "## Evidence Strength\n\n**Moderate — Single source**" in answer.text
    assert "The source directly bears" not in answer.text


def test_repaired_attempt_passes_at_four() -> None:
    request, initial = answer_fixture()
    revised = initial.model_copy(update={"paragraphs": (
        initial.paragraphs[0].model_copy(update={"paragraph_id": uuid4()}),
    )})
    answer = asyncio.run(AnswerCoordinator(
        Synthesis(initial, [audit(initial, 3), audit(revised, 4)], revised)
    ).answer(request))

    assert answer.draft is revised
    assert not answer.degraded and not answer.revision_failed
    assert "## Evidence Strength\n\n**Moderate — Single source**" in answer.text


def test_audit_retries_twice_before_accepting() -> None:
    request, draft = answer_fixture()
    synthesis = Synthesis(draft, [
        InvalidAnswerProposalError("bad one"),
        InvalidAnswerProposalError("bad two"),
        audit(draft, 5),
    ])
    answer = asyncio.run(AnswerCoordinator(synthesis).answer(request))

    assert synthesis.audit_calls == 3
    assert answer.final_audit.answer_quality == 5


def test_exhausted_repair_publishes_earliest_best_attempt_with_warning() -> None:
    request, initial = answer_fixture()
    revised = initial.model_copy(update={"paragraphs": (
        initial.paragraphs[0].model_copy(update={"paragraph_id": uuid4()}),
    )})
    synthesis = Synthesis(initial, [
        audit(initial, 3),
        InvalidAnswerProposalError("bad one"),
        InvalidAnswerProposalError("bad two"),
        InvalidAnswerProposalError("bad three"),
    ], revised)

    answer = asyncio.run(AnswerCoordinator(synthesis).answer(request))

    assert answer.draft is initial
    assert not answer.degraded and answer.revision_failed
    assert answer.text.startswith("## Answer\n\n> **Validation warning:**")
    assert "Please ask follow-up questions" in answer.text
    assert "## Evidence Strength\n\n**Limited — Unsupported claims**" in answer.text


def test_attempts_below_three_fail_closed() -> None:
    request, initial = answer_fixture()
    revised = initial.model_copy(update={"paragraphs": (
        initial.paragraphs[0].model_copy(update={"paragraph_id": uuid4()}),
    )})
    answer = asyncio.run(AnswerCoordinator(
        Synthesis(initial, [audit(initial, 2), audit(revised, 2)], revised)
    ).answer(request))

    assert answer.degraded and answer.revision_failed
    assert answer.text.startswith("I'm sorry, we couldn't complete your request.")
