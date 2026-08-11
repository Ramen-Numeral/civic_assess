from time import perf_counter
from pydantic import BaseModel, ConfigDict

from app.domain.validation import NonBlankText
from app.features.answer_synthesis.errors import AnswerSynthesisError, InvalidAnswerProposalError
from app.features.answer_synthesis.renderer import cited_sources, render_grounded_answer
from app.features.answer_synthesis.schemas import (
    AnswerAudit, GroundedAnswerRequest, NaturalAnswerDraft,
)
from app.features.answer_synthesis.service import AnswerSynthesisService


class GroundedAnswerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_draft: NaturalAnswerDraft
    draft: NaturalAnswerDraft
    initial_audit: AnswerAudit | None = None
    final_audit: AnswerAudit | None = None
    revision_attempted: bool = False
    revision_failed: bool = False
    degraded: bool = False
    drafting_ms: float = 0
    audit_ms: float = 0
    revision_ms: float = 0
    text: NonBlankText

class AnswerCoordinator:
    def __init__(self, synthesis: AnswerSynthesisService) -> None:
        self._synthesis = synthesis

    async def answer(self, request: GroundedAnswerRequest) -> GroundedAnswerResult:
        started = perf_counter()
        initial = await self._synthesis.draft(request)
        drafting_ms = _elapsed(started)
        audit_started = perf_counter()
        first = await self._audit(request, initial)
        draft, final, revision_attempted, revision_ms = initial, first, False, 0
        if first and first.verdict == "revise":
            revision_attempted, revision_started = True, perf_counter()
            try:
                draft = await self._synthesis.revise(request, initial, first)
                final = await self._audit(request, draft)
            except (AnswerSynthesisError, InvalidAnswerProposalError):
                draft, final = initial, first
            revision_ms = _elapsed(revision_started)
        degraded = bool(final and final.unsupported_paragraph_ids)
        revision_failed = bool(revision_attempted and final and final.verdict == "revise")
        note = _evidence_note(request, draft, final, degraded)
        return GroundedAnswerResult(
            initial_draft=initial, draft=draft, initial_audit=first, final_audit=final,
            revision_attempted=revision_attempted, revision_failed=revision_failed,
            degraded=degraded, drafting_ms=drafting_ms,
            audit_ms=_elapsed(audit_started) - revision_ms, revision_ms=revision_ms,
            text=render_grounded_answer(
                NaturalAnswerDraft(paragraphs=()) if degraded else draft,
                request.evidence,
                final.paragraph_quotes if final and not degraded else {},
                note,
            ),
        )

    async def _audit(
        self, request: GroundedAnswerRequest, draft: NaturalAnswerDraft,
    ) -> AnswerAudit | None:
        if not draft.paragraphs:
            return None
        try:
            return await self._synthesis.audit(request, draft)
        except (AnswerSynthesisError, InvalidAnswerProposalError):
            return None


def _evidence_note(
    request: GroundedAnswerRequest,
    draft: NaturalAnswerDraft,
    audit: AnswerAudit | None,
    degraded: bool,
) -> str:
    if degraded or not draft.paragraphs:
        return "The available evidence could not support a reliable answer."
    if audit is None:
        return "The answer could not receive a final evidence audit."
    findings = [
        request.findings[index]
        for index in {index for item in draft.paragraphs for index in item.finding_indexes}
    ]
    sources = cited_sources(draft, request.evidence)
    if min(audit.paragraph_support.values()) <= 3:
        strength = "The cited sources only partially establish this answer"
    elif any(item.evidence_basis == "projected" for item in findings):
        strength = (
            "The cited sources support this answer, except where it describes "
            "projections rather than measured outcomes"
        )
    elif any(item.source_fitness == "qualified" for item in findings):
        strength = "The cited sources support this answer within the limitations noted"
    else:
        strength = "The cited sources directly document this answer"
    plural = "" if len(sources) == 1 else "s"
    parts = [f"{strength}, drawn from {len(sources)} source{plural}.", audit.evidence_note]
    if request.unresolved:
        gaps = "; ".join(item.rstrip(".") for item in request.unresolved)
        parts.append(f"The available evidence did not establish: {gaps}.")
    return " ".join(parts)


def _elapsed(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)
