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
