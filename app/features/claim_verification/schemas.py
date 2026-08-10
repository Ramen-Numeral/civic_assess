from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.domain.validation import NonBlankText
from app.features.answer_synthesis.schemas import GroundedAnswerDraft
from app.features.evidence_retrieval.schemas import EvidenceCandidate


EntailmentVerdict = Literal[
    "entailed", "contradicted", "insufficient_evidence"
]


class ClaimVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft: GroundedAnswerDraft
    evidence: tuple[EvidenceCandidate, ...]

    @model_validator(mode="after")
    def require_available_evidence(self) -> "ClaimVerificationRequest":
        claim_ids = [claim.claim_id for claim in self.draft.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("Answer claim IDs must be unique")
        chunk_ids = {item.chunk_id for item in self.evidence}
        if len(chunk_ids) != len(self.evidence):
            raise ValueError("Verification evidence chunk IDs must be unique")
        if any(
            chunk_id not in chunk_ids
            for claim in self.draft.claims
            for chunk_id in claim.supporting_chunk_ids
        ):
            raise ValueError("Answer claim cites unavailable verification evidence")
        return self


class CitationVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: UUID
    verdict: EntailmentVerdict
    entailment_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    contradiction_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    neutral_score: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_probability_distribution(self) -> "CitationVerification":
        if abs(
            self.entailment_score
            + self.contradiction_score
            + self.neutral_score
            - 1
        ) > 1e-5:
            raise ValueError("Citation scores must form one probability distribution")
        return self


class TextPairVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: EntailmentVerdict
    entailment_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    contradiction_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    neutral_score: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_probability_distribution(self) -> "TextPairVerification":
        if abs(
            self.entailment_score
            + self.contradiction_score
            + self.neutral_score
            - 1
        ) > 1e-5:
            raise ValueError("Text-pair scores must form one probability distribution")
        return self


class ConflictCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left_claim_id: UUID
    right_claim_id: UUID
    left_to_right: TextPairVerification
    right_to_left: TextPairVerification

    @computed_field
    @property
    def contradiction_score(self) -> float:
        return max(
            self.left_to_right.contradiction_score,
            self.right_to_left.contradiction_score,
        )


class ClaimVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: UUID
    verdict: EntailmentVerdict
    citations: tuple[CitationVerification, ...] = Field(min_length=1)


class ClaimVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_version: NonBlankText
    claims: tuple[ClaimVerification, ...]

    @computed_field
    @property
    def fully_entailed(self) -> bool:
        return bool(self.claims) and all(
            claim.verdict == "entailed" for claim in self.claims
        )
