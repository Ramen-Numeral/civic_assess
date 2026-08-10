from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from app.features.answer_synthesis.errors import (
    AnswerSynthesisError,
    InvalidAnswerProposalError,
)
from app.features.answer_synthesis.schemas import (
    AtomicAnswerClaim,
    GroundedAnswerDraft,
    GroundedAnswerCompositionProposal,
    GroundedAnswerProposal,
    GroundedAnswerRepairProposal,
    GroundedAnswerRequest,
    RepairedAnswerClaim,
    RepairedAnswerDraft,
)
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.errors import FailureKind, LLMError
from app.prompts.base import Prompt

if TYPE_CHECKING:
    from app.features.claim_verification.schemas import (
        ClaimVerificationResult,
        ConflictCandidate,
    )


class AnswerSynthesisService:
    def __init__(
        self,
        *,
        llm: LLMClient,
        prompt: Prompt,
        repair_prompt: Prompt,
        composition_prompt: Prompt,
    ) -> None:
        self._llm = llm
        self._prompt = prompt
        self._repair_prompt = repair_prompt
        self._composition_prompt = composition_prompt

    async def draft(self, request: GroundedAnswerRequest) -> GroundedAnswerDraft:
        if not request.coverage.findings:
            return GroundedAnswerDraft(claims=())
        requirements = {
            f"R{position}": item
            for position, item in enumerate(request.requirements, 1)
        }
        requirement_refs = {
            item.requirement_id: ref for ref, item in requirements.items()
        }
        evidence = {
            f"E{position}": item
            for position, item in enumerate(request.evidence, 1)
        }
        evidence_refs = {item.chunk_id: ref for ref, item in evidence.items()}
        grounded = {
            finding.requirement_id for finding in request.coverage.findings
        }
        messages = [
            SystemMessage(content=self._prompt.build()),
            HumanMessage(content=json.dumps({
                "canonical_query": request.canonical_query,
                "requirements": [{
                    "ref": ref,
                    "description": item.description,
                    "evidence_expectation": item.evidence_expectation,
                } for ref, item in requirements.items()],
                "coverage": {
                    "findings": [{
                        "requirement_ref": requirement_refs[item.requirement_id],
                        "evidence_refs": [
                            evidence_refs[chunk_id]
                            for chunk_id in item.supporting_chunk_ids
                        ],
                        "evidence_basis": item.evidence_basis,
                        "source_fitness": item.source_fitness,
                        "qualification": item.qualification,
                    } for item in request.coverage.findings],
                    "gaps": [{
                        "requirement_ref": requirement_refs[item.requirement_id],
                        "description": item.description,
                        "missing_evidence": item.missing_evidence,
                    } for item in request.coverage.gaps],
                },
                "evidence": [{
                    "ref": ref,
                    "title": item.title,
                    "canonical_url": str(item.canonical_url),
                    "heading_path": item.heading_path,
                    "text": item.text,
                } for ref, item in evidence.items()],
            }, ensure_ascii=False)),
        ]
        try:
            proposal = await self._llm.invoke_structured(
                messages, GroundedAnswerProposal,
            )
            if any(
                requirements[item.requirement_ref].requirement_id not in grounded
                for item in proposal.claims
            ):
                raise ValueError("Answer claim targets an ungrounded requirement")
            return GroundedAnswerDraft(claims=tuple(
                AtomicAnswerClaim(
                    claim_id=uuid4(),
                    requirement_id=requirements[item.requirement_ref].requirement_id,
                    text=item.text,
                    supporting_chunk_ids=tuple(
                        evidence[ref].chunk_id for ref in item.evidence_refs
                    ),
                )
                for item in proposal.claims
            ))
        except LLMError as exc:
            if exc.failures and all(
                failure.kind is FailureKind.INVALID_OUTPUT
                for failure in exc.failures
            ):
                raise InvalidAnswerProposalError(
                    "Answer writer returned invalid structured output"
                ) from exc
            raise AnswerSynthesisError(
                "answer_writer_unavailable",
                "I couldn't prepare a grounded answer right now.",
                retryable=True,
            ) from exc
        except (KeyError, ValueError) as exc:
            raise InvalidAnswerProposalError(
                "Answer output violated grounding invariants"
            ) from exc

    async def repair(
        self,
        request: GroundedAnswerRequest,
        failed_draft: GroundedAnswerDraft,
        verification: ClaimVerificationResult,
    ) -> RepairedAnswerDraft:
        failed = {claim.claim_id: claim for claim in failed_draft.claims}
        verified = {item.claim_id: item for item in verification.claims}
        if (
            tuple(failed) != tuple(verified)
            or any(item.verdict == "entailed" for item in verified.values())
            or any(
                tuple(citation.chunk_id for citation in verified[claim.claim_id].citations)
                != claim.supporting_chunk_ids
                for claim in failed.values()
            )
        ):
            raise ValueError("Repair requires aligned failed claims and verifications")
        available = {item.chunk_id for item in request.evidence}
        grounded = {item.requirement_id for item in request.coverage.findings}
        if any(
            claim.requirement_id not in grounded
            or any(chunk_id not in available for chunk_id in claim.supporting_chunk_ids)
            for claim in failed.values()
        ):
            raise ValueError("Repair claims must retain available grounded ownership")
        if not failed:
            return RepairedAnswerDraft(replacements=())

        claims = {f"C{index}": claim for index, claim in enumerate(failed.values(), 1)}
        evidence = {f"E{index}": item for index, item in enumerate(request.evidence, 1)}
        requirements = {item.requirement_id: item for item in request.requirements}
        messages = [
            SystemMessage(content=self._repair_prompt.build()),
            HumanMessage(content=json.dumps({
                "canonical_query": request.canonical_query,
                "failed_claims": [{
                    "ref": ref,
                    "text": claim.text,
                    "requirement": {
                        "description": requirements[claim.requirement_id].description,
                        "evidence_expectation": requirements[
                            claim.requirement_id
                        ].evidence_expectation,
                    },
                    "verification": verified[claim.claim_id].model_dump(mode="json"),
                } for ref, claim in claims.items()],
                "evidence": [{
                    "ref": ref,
                    "title": item.title,
                    "canonical_url": str(item.canonical_url),
                    "heading_path": item.heading_path,
                    "text": item.text,
                } for ref, item in evidence.items()],
            }, ensure_ascii=False)),
        ]
        try:
            proposal = await self._llm.invoke_structured(
                messages, GroundedAnswerRepairProposal,
            )
            replacements = tuple(
                RepairedAnswerClaim(
                    replaces_claim_id=claims[item.replaces_claim_ref].claim_id,
                    claim=AtomicAnswerClaim(
                        claim_id=uuid4(),
                        requirement_id=claims[
                            item.replaces_claim_ref
                        ].requirement_id,
                        text=item.text,
                        supporting_chunk_ids=tuple(
                            evidence[ref].chunk_id for ref in item.evidence_refs
                        ),
                    ),
                )
                for item in proposal.claims
            )
            normalized = [
                " ".join(item.claim.text.split()).casefold()
                for item in replacements
            ]
            if len(normalized) != len(set(normalized)):
                raise ValueError("Repair claims must be distinct")
            return RepairedAnswerDraft(replacements=replacements)
        except LLMError as exc:
            if exc.failures and all(
                failure.kind is FailureKind.INVALID_OUTPUT
                for failure in exc.failures
            ):
                raise InvalidAnswerProposalError(
                    "Answer repair returned invalid structured output"
                ) from exc
            raise AnswerSynthesisError(
                "answer_writer_unavailable",
                "I couldn't repair the grounded answer right now.",
                retryable=True,
            ) from exc
        except (KeyError, ValueError) as exc:
            raise InvalidAnswerProposalError(
                "Answer repair violated grounding invariants"
            ) from exc

    async def compose(
        self,
        request: GroundedAnswerRequest,
        draft: GroundedAnswerDraft,
        conflicts: tuple[ConflictCandidate, ...],
    ) -> GroundedAnswerDraft:
        if not draft.claims:
            return draft
        claims = {f"C{index}": claim for index, claim in enumerate(draft.claims, 1)}
        claim_refs = {claim.claim_id: ref for ref, claim in claims.items()}
        requirements = {item.requirement_id: item for item in request.requirements}
        evidence = {item.chunk_id: item for item in request.evidence}
        messages = [
            SystemMessage(content=self._composition_prompt.build()),
            HumanMessage(content=json.dumps({
                "canonical_query": request.canonical_query,
                "claims": [{
                    "ref": ref,
                    "text": claim.text,
                    "requirement": requirements[claim.requirement_id].description,
                    "sources": [evidence[item].title for item in claim.supporting_chunk_ids],
                } for ref, claim in claims.items()],
                "conflict_candidates": [{
                    "left_claim_ref": claim_refs[item.left_claim_id],
                    "right_claim_ref": claim_refs[item.right_claim_id],
                    "contradiction_score": item.contradiction_score,
                } for item in conflicts],
                "limitations": [gap.missing_evidence for gap in request.coverage.gaps],
            }, ensure_ascii=False)),
        ]
        try:
            proposal = await self._llm.invoke_structured(
                messages, GroundedAnswerCompositionProposal,
            )
            if tuple(item.claim_ref for item in proposal.sentences) != tuple(claims):
                raise ValueError("Composition must preserve claim order and identity")
            return GroundedAnswerDraft(claims=tuple(
                claims[item.claim_ref].model_copy(update={"text": item.text})
                for item in proposal.sentences
            ))
        except LLMError as exc:
            if exc.failures and all(
                failure.kind is FailureKind.INVALID_OUTPUT
                for failure in exc.failures
            ):
                raise InvalidAnswerProposalError(
                    "Answer composition returned invalid structured output"
                ) from exc
            raise AnswerSynthesisError(
                "answer_writer_unavailable",
                "I couldn't compose the grounded answer right now.",
                retryable=True,
            ) from exc
        except (KeyError, ValueError) as exc:
            raise InvalidAnswerProposalError(
                "Answer composition violated claim alignment"
            ) from exc
