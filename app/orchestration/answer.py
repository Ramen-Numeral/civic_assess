from time import perf_counter
from pydantic import BaseModel, ConfigDict

from app.domain.validation import NonBlankText
from app.features.answer_synthesis.errors import AnswerSynthesisError, InvalidAnswerProposalError
from app.features.answer_synthesis.renderer import render_grounded_answer
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
        note = ("The available evidence could not support a reliable answer."
                if degraded else final.evidence_note if final else
                "The answer could not receive a final evidence audit.")
        return GroundedAnswerResult(
            initial_draft=initial, draft=draft, initial_audit=first, final_audit=final,
            revision_attempted=revision_attempted, revision_failed=revision_failed,
            degraded=degraded, drafting_ms=drafting_ms,
            audit_ms=_elapsed(audit_started) - revision_ms, revision_ms=revision_ms,
            text=render_grounded_answer(
                NaturalAnswerDraft(paragraphs=()) if degraded else draft,
                request.evidence, note,
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


def _elapsed(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)
