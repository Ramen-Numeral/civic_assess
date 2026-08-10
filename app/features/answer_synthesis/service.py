import json
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from app.features.answer_synthesis.errors import (
    AnswerSynthesisError,
    InvalidAnswerProposalError,
)
from app.features.answer_synthesis.schemas import (
    AtomicAnswerClaim,
    GroundedAnswerDraft,
    GroundedAnswerProposal,
    GroundedAnswerRequest,
)
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.errors import FailureKind, LLMError
from app.prompts.base import Prompt


class AnswerSynthesisService:
    def __init__(self, *, llm: LLMClient, prompt: Prompt) -> None:
        self._llm = llm
        self._prompt = prompt

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
