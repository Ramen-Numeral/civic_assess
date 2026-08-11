from time import perf_counter
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.validation import NonBlankText
from app.features.answer_synthesis.errors import (
    AnswerSynthesisError,
    InvalidAnswerProposalError,
)
from app.features.answer_synthesis.renderer import render_grounded_answer
from app.features.answer_synthesis.schemas import (
    AtomicAnswerClaim,
    GroundedAnswerDraft,
    GroundedAnswerRequest,
)
from app.features.answer_synthesis.service import AnswerSynthesisService
from app.features.claim_verification.schemas import (
    ClaimVerification,
    ClaimVerificationRequest,
    ClaimVerificationResult,
    ConflictCandidate,
)
from app.features.claim_verification.service import ClaimVerificationService
from app.features.evidence_coverage.schemas import EvidenceCoverageAssessment


class GroundedAnswerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    coverage: EvidenceCoverageAssessment
    draft: GroundedAnswerDraft
    verification: ClaimVerificationResult
    discarded_claim_ids: tuple[UUID, ...]
    repair_attempted: bool
    drafted_claim_count: int = 0
    initial_verification: ClaimVerificationResult | None = None
    replacement_verification: ClaimVerificationResult | None = None
    drafting_ms: float = 0
    scaffold_verification_ms: float = 0
    conflict_candidates: tuple[ConflictCandidate, ...] = ()
    rewritten_claim_ids: tuple[UUID, ...] = ()
    fallback_claim_ids: tuple[UUID, ...] = ()
    conflict_screening_ms: float = 0
    composition_ms: float = 0
    postcomposition_nli_ms: float = 0
    text: NonBlankText

    @model_validator(mode="after")
    def require_aligned_verified_draft(self) -> "GroundedAnswerResult":
        draft_ids = tuple(item.claim_id for item in self.draft.claims)
        verified_ids = tuple(item.claim_id for item in self.verification.claims)
        if draft_ids != verified_ids or any(
            item.verdict != "entailed" for item in self.verification.claims
        ):
            raise ValueError("Final answer claims require aligned entailment")
        if len(set(self.discarded_claim_ids)) != len(self.discarded_claim_ids):
            raise ValueError("Discarded claim IDs must be unique")
        if set(draft_ids) & set(self.discarded_claim_ids):
            raise ValueError("Surviving claims cannot be discarded")
        rewritten, fallback = set(self.rewritten_claim_ids), set(self.fallback_claim_ids)
        if rewritten & fallback or rewritten | fallback != set(draft_ids):
            raise ValueError("Every final claim must be rewritten or fallback")
        return self


class AnswerCoordinator:
    def __init__(
        self,
        synthesis: AnswerSynthesisService,
        verification: ClaimVerificationService,
    ) -> None:
        self._synthesis = synthesis
        self._verification = verification

    async def answer(self, request: GroundedAnswerRequest) -> GroundedAnswerResult:
        started = perf_counter()
        initial_draft = await self._synthesis.draft(request)
        drafting_ms = _elapsed(started)
        started = perf_counter()
        initial = await self._verification.verify(ClaimVerificationRequest(
            draft=initial_draft, evidence=request.evidence,
        ))
        _require_alignment(initial_draft, initial)
        failed_claims = tuple(
            claim for claim, result in zip(
                initial_draft.claims, initial.claims, strict=True
            ) if result.verdict != "entailed"
        )
        failed_results = tuple(
            result for result in initial.claims if result.verdict != "entailed"
        )
        replacement_claims = ()
        replacement_results = ()
        replacement_verification = None
        if failed_claims:
            failed_draft = GroundedAnswerDraft(claims=failed_claims)
            failed_verification = ClaimVerificationResult(
                model_version=initial.model_version, claims=failed_results,
            )
            try:
                repair = await self._synthesis.repair(
                    request, failed_draft, failed_verification,
                )
            except (AnswerSynthesisError, InvalidAnswerProposalError):
                repair = None
            if repair and repair.replacements:
                replacement_draft = GroundedAnswerDraft(
                    claims=tuple(item.claim for item in repair.replacements)
                )
                replacement_verification = await self._verification.verify(
                    ClaimVerificationRequest(
                        draft=replacement_draft, evidence=request.evidence,
                    )
                )
                _require_alignment(replacement_draft, replacement_verification)
                if replacement_verification.model_version != initial.model_version:
                    raise ValueError("Answer verification model changed during repair")
                replacement_claims = repair.replacements
                replacement_results = replacement_verification.claims

        replacement_map: dict[
            UUID, list[tuple[AtomicAnswerClaim, ClaimVerification]]
        ] = {}
        for replacement, result in zip(
            replacement_claims, replacement_results, strict=True
        ):
            if result.verdict == "entailed":
                replacement_map.setdefault(
                    replacement.replaces_claim_id, []
                ).append((replacement.claim, result))
        final_claims, final_results, discarded = [], [], []
        normalized: set[str] = set()
        for claim, result in zip(initial_draft.claims, initial.claims, strict=True):
            candidates = (
                [(claim, result)] if result.verdict == "entailed"
                else replacement_map.get(claim.claim_id, [])
            )
            survived = False
            for candidate, candidate_result in candidates:
                key = " ".join(candidate.text.split()).casefold()
                if key in normalized or len(final_claims) == 20:
                    continue
                normalized.add(key)
                final_claims.append(candidate)
                final_results.append(candidate_result)
                survived = True
            if result.verdict != "entailed" and not survived:
                discarded.append(claim.claim_id)
        draft = GroundedAnswerDraft(claims=tuple(final_claims))
        verification = ClaimVerificationResult(
            model_version=initial.model_version, claims=tuple(final_results),
        )
        scaffold_ms = _elapsed(started)
        started = perf_counter()
        conflicts = await self._verification.screen_conflicts(draft)
        conflict_ms = _elapsed(started)
        started = perf_counter()
        try:
            composed = await self._synthesis.compose(request, draft, conflicts)
        except (AnswerSynthesisError, InvalidAnswerProposalError):
            composed = None
        composition_ms = _elapsed(started)
        rewritten_ids: list[UUID] = []
        fallback_ids: list[UUID] = []
        if composed is not None:
            _require_claim_identity(draft, composed)
            started = perf_counter()
            fidelity = await self._verification.verify_fidelity(tuple(
                (source.text, rewrite.text)
                for source, rewrite in zip(
                    draft.claims, composed.claims, strict=True
                )
            ))
            composed_verification = await self._verification.verify(
                ClaimVerificationRequest(draft=composed, evidence=request.evidence)
            )
            _require_alignment(composed, composed_verification)
            if composed_verification.model_version != verification.model_version:
                raise ValueError("Answer verification model changed during composition")
            final_claims, final_results = [], []
            for source, source_result, rewrite, pair, rewrite_result in zip(
                draft.claims,
                verification.claims,
                composed.claims,
                fidelity,
                composed_verification.claims,
                strict=True,
            ):
                accepted = (
                    all(item.verdict == "entailed" for item in pair)
                    and rewrite_result.verdict == "entailed"
                )
                final_claims.append(rewrite if accepted else source)
                final_results.append(rewrite_result if accepted else source_result)
                (rewritten_ids if accepted else fallback_ids).append(source.claim_id)
            draft = GroundedAnswerDraft(claims=tuple(final_claims))
            verification = ClaimVerificationResult(
                model_version=verification.model_version,
                claims=tuple(final_results),
            )
            postcomposition_ms = _elapsed(started)
        else:
            fallback_ids.extend(item.claim_id for item in draft.claims)
            postcomposition_ms = 0
        return GroundedAnswerResult(
            coverage=request.coverage,
            draft=draft,
            verification=verification,
            discarded_claim_ids=tuple(discarded),
            repair_attempted=bool(failed_claims),
            drafted_claim_count=len(initial_draft.claims),
            initial_verification=initial,
            replacement_verification=replacement_verification,
            drafting_ms=drafting_ms,
            scaffold_verification_ms=scaffold_ms,
            conflict_candidates=conflicts,
            rewritten_claim_ids=tuple(rewritten_ids),
            fallback_claim_ids=tuple(fallback_ids),
            conflict_screening_ms=conflict_ms,
            composition_ms=composition_ms,
            postcomposition_nli_ms=postcomposition_ms,
            text=render_grounded_answer(draft, request.coverage, request.evidence),
        )


def _require_alignment(
    draft: GroundedAnswerDraft, verification: ClaimVerificationResult
) -> None:
    if tuple(item.claim_id for item in draft.claims) != tuple(
        item.claim_id for item in verification.claims
    ):
        raise ValueError("Claim verification does not align with its draft")


def _require_claim_identity(
    source: GroundedAnswerDraft, rewrite: GroundedAnswerDraft
) -> None:
    if any(
        left.claim_id != right.claim_id
        or left.requirement_id != right.requirement_id
        or left.supporting_chunk_ids != right.supporting_chunk_ids
        for left, right in zip(source.claims, rewrite.claims, strict=True)
    ):
        raise ValueError("Composition changed authoritative claim identity")


def _elapsed(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)
