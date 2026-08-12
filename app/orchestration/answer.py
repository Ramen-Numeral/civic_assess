import logging
from time import perf_counter
from pydantic import BaseModel, ConfigDict

from app.domain.validation import NonBlankText
from app.features.answer_synthesis.errors import AnswerSynthesisError, InvalidAnswerProposalError
from app.features.answer_synthesis.renderer import cited_sources, render_grounded_answer
from app.features.answer_synthesis.schemas import (
    AnswerAudit, GroundedAnswerRequest, NaturalAnswerDraft,
)
from app.features.answer_synthesis.service import AnswerSynthesisService


LOGGER = logging.getLogger(__name__)
AUDIT_ATTEMPTS = 3
INCOMPLETE_VALIDATION_WARNING = (
    "The system could not fully validate this response. Please ask follow-up "
    "questions to clarify any important claims."
)
UNABLE_TO_COMPLETE_MESSAGE = (
    "I'm sorry, we couldn't complete your request. Would you like to try asking "
    "something else or a similar question?"
)


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
        accepted = bool(first and first.passes(5))
        validation_incomplete = False
        if first and not accepted:
            revision_attempted, revision_started = True, perf_counter()
            revised = None
            revised_audit = None
            try:
                revised = await self._synthesis.revise(request, initial, first)
                revised_audit = await self._audit(request, revised)
            except (AnswerSynthesisError, InvalidAnswerProposalError):
                pass
            revision_ms = _elapsed(revision_started)
            accepted = bool(revised_audit and revised_audit.passes(4))
            if accepted:
                draft, final = revised, revised_audit
            else:
                candidates = [(initial, first)]
                if revised is not None and revised_audit is not None:
                    candidates.append((revised, revised_audit))
                eligible = [
                    candidate for candidate in candidates
                    if candidate[1].answer_quality >= 3
                ]
                if eligible:
                    # max() retains the first item when scores tie, preferring the
                    # earliest draft as the safer known version.
                    draft, final = max(
                        eligible, key=lambda candidate: candidate[1].answer_quality,
                    )
                    validation_incomplete = True
        degraded = not accepted and not validation_incomplete
        revision_failed = revision_attempted and not accepted
        note = _evidence_note(request, draft, final, degraded)
        return GroundedAnswerResult(
            initial_draft=initial, draft=draft, initial_audit=first, final_audit=final,
            revision_attempted=revision_attempted, revision_failed=revision_failed,
            degraded=degraded, drafting_ms=drafting_ms,
            audit_ms=_elapsed(audit_started) - revision_ms, revision_ms=revision_ms,
            text=(UNABLE_TO_COMPLETE_MESSAGE if degraded else render_grounded_answer(
                draft, request.evidence, note,
                INCOMPLETE_VALIDATION_WARNING if validation_incomplete else None,
            )),
        )

    async def _audit(
        self, request: GroundedAnswerRequest, draft: NaturalAnswerDraft,
    ) -> AnswerAudit | None:
        if not draft.paragraphs:
            return None
        expected = ", ".join(f"P{index}" for index in range(1, len(draft.paragraphs) + 1))
        validation_feedback = None
        for attempt in range(1, AUDIT_ATTEMPTS + 1):
            try:
                return await self._synthesis.audit(
                    request, draft, validation_feedback=validation_feedback,
                )
            except (AnswerSynthesisError, InvalidAnswerProposalError) as exc:
                LOGGER.warning(
                    "Answer audit attempt failed",
                    extra={
                        "event": "answer.audit.attempt_failed",
                        "attempt": attempt,
                        "max_attempts": AUDIT_ATTEMPTS,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
                validation_feedback = (
                    f"The prior audit was rejected: {exc}. Return exactly one "
                    "paragraph_support item for each proposed paragraph, in this "
                    f"order: {expected}. Do not omit, duplicate, or reorder refs."
                )
        LOGGER.error(
            "Answer audit retries exhausted",
            extra={
                "event": "answer.audit.retries_exhausted",
                "attempts": AUDIT_ATTEMPTS,
            },
        )
        return None


def _evidence_note(
    request: GroundedAnswerRequest,
    draft: NaturalAnswerDraft,
    audit: AnswerAudit | None,
    degraded: bool,
) -> str:
    if degraded or not draft.paragraphs:
        return (
            "**Limited — Insufficient evidence**\n\nAvailable evidence was not "
            "sufficient to provide a complete, well-supported answer."
        )
    if audit is None:
        return (
            "**Limited — Review incomplete**\n\nThe evidence check couldn't be "
            "completed, so the overall strength of support remains uncertain."
        )
    findings = [
        request.findings[index]
        for index in {index for item in draft.paragraphs for index in item.finding_indexes}
    ]
    support = min(audit.paragraph_support.values(), default=0)
    if min(support, audit.answer_quality) <= 3:
        return (
            "**Limited — Unsupported claims**\n\nSome parts are supported, but key "
            "claims could not be fully supported by the available evidence."
        )
    if request.unresolved:
        return (
            "**Moderate — Unresolved details**\n\nCore claims are supported, but "
            "part of your question remains unanswered."
        )
    if any(item.evidence_basis == "projected" for item in findings):
        return (
            "**Moderate — Based on forecasts**\n\nCore claims are supported, but "
            "some conclusions rely on projected rather than documented outcomes."
        )
    if any(item.source_fitness == "qualified" for item in findings):
        return (
            "**Moderate — Evidence caveats**\n\nCore claims are supported, but "
            "some supporting evidence has limitations to keep in mind."
        )
    if len(cited_sources(draft, request.evidence)) < 2:
        return (
            "**Moderate — Single source**\n\nCore claims are supported, but rely "
            "on a single source without confirmation from another source."
        )
    if min(support, audit.answer_quality) < 5:
        return (
            "**Moderate — Minor gaps**\n\nCore claims are supported, though the "
            "final check identified minor gaps or qualifications."
        )
    return (
        "**Strong — Well-supported**\n\nMain claims are backed by multiple cited "
        "sources, with no material gaps or reliance on projected outcomes."
    )


def _elapsed(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)
