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
        initial_draft = await self._synthesis.draft(request)
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
        return GroundedAnswerResult(
            coverage=request.coverage,
            draft=draft,
            verification=verification,
            discarded_claim_ids=tuple(discarded),
            repair_attempted=bool(failed_claims),
            text=render_grounded_answer(draft, request.coverage, request.evidence),
        )


def _require_alignment(
    draft: GroundedAnswerDraft, verification: ClaimVerificationResult
) -> None:
    if tuple(item.claim_id for item in draft.claims) != tuple(
        item.claim_id for item in verification.claims
    ):
        raise ValueError("Claim verification does not align with its draft")
