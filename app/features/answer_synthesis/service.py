import json
import logging
import re
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from app.features.answer_synthesis.errors import AnswerSynthesisError, InvalidAnswerProposalError
from app.features.answer_synthesis.schemas import (
    AnswerAudit, AnswerAuditProposal, AnswerParagraph, GroundedAnswerProposal,
    GroundedAnswerRequest, NaturalAnswerDraft,
)
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.errors import FailureKind, LLMError
from app.prompts.base import Prompt


LOGGER = logging.getLogger(__name__)
INTERNAL_FINDING_REF = re.compile(r"\s*[\(\[]F[1-9]\d*[\)\]]")


class AnswerSynthesisService:
    def __init__(
        self, *, llm: LLMClient, audit_llm: LLMClient,
        prompt: Prompt, audit_prompt: Prompt,
    ) -> None:
        self._llm, self._audit_llm = llm, audit_llm
        self._prompt, self._audit_prompt = prompt, audit_prompt

    async def draft(self, request: GroundedAnswerRequest) -> NaturalAnswerDraft:
        if not request.findings:
            return NaturalAnswerDraft(paragraphs=())
        return await self._write(request)

    async def revise(
        self, request: GroundedAnswerRequest, draft: NaturalAnswerDraft,
        audit: AnswerAudit,
    ) -> NaturalAnswerDraft:
        return await self._write(request, draft, audit.revision_instructions)

    async def audit(
        self, request: GroundedAnswerRequest, draft: NaturalAnswerDraft,
    ) -> AnswerAudit:
        payload, _, evidence_refs = _answer_view(request)
        paragraphs = {f"P{i}": item for i, item in enumerate(draft.paragraphs, 1)}
        payload["proposed_answer"] = [{
            "ref": ref, "text": item.text,
            "finding_refs": [f"F{i + 1}" for i in item.finding_indexes],
            "evidence_refs": [evidence_refs[c] for c in item.supporting_chunk_ids],
        } for ref, item in paragraphs.items()]
        try:
            proposal = await self._audit_llm.invoke_structured([
                SystemMessage(content=self._audit_prompt.build()),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ], AnswerAuditProposal)
            ratings = {item.paragraph_ref: item.rating for item in proposal.paragraph_support}
            if tuple(ratings) != tuple(paragraphs):
                raise ValueError("Audit must rate every paragraph exactly once and in order")
            return AnswerAudit(
                paragraph_support={
                    paragraphs[ref].paragraph_id: rating
                    for ref, rating in ratings.items()
                },
                answer_quality=proposal.answer_quality,
                revision_instructions=proposal.revision_instructions,
                evidence_note=INTERNAL_FINDING_REF.sub("", proposal.evidence_note).strip(),
            )
        except LLMError as exc:
            self._raise_model_error(exc, "Answer auditor")
        except (KeyError, ValueError) as exc:
            raise InvalidAnswerProposalError("Answer audit violated paragraph alignment") from exc

    async def _write(
        self, request: GroundedAnswerRequest, draft: NaturalAnswerDraft | None = None,
        instructions: tuple[str, ...] = (),
    ) -> NaturalAnswerDraft:
        payload, findings, _ = _answer_view(request)
        if draft is not None:
            payload["proposed_answer"] = [{
                "ref": f"P{i}", "text": item.text,
                "finding_refs": [f"F{x + 1}" for x in item.finding_indexes],
            } for i, item in enumerate(draft.paragraphs, 1)]
            payload["revision_instructions"] = list(instructions)
        messages = [
            SystemMessage(content=self._prompt.build()),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
        for attempt in range(2):
            try:
                proposal = await self._llm.invoke_structured(
                    messages, GroundedAnswerProposal,
                )
                return NaturalAnswerDraft(paragraphs=tuple(
                    AnswerParagraph(
                        paragraph_id=uuid4(),
                        text=INTERNAL_FINDING_REF.sub("", item.text).strip(),
                        finding_indexes=tuple(findings[ref] for ref in item.finding_refs),
                        supporting_chunk_ids=tuple(dict.fromkeys(
                            chunk for ref in item.finding_refs
                            for chunk in request.findings[
                                findings[ref]
                            ].supporting_chunk_ids
                        )),
                    ) for item in proposal.paragraphs
                ))
            except LLMError as exc:
                if not exc.failures or any(
                    failure.kind is not FailureKind.INVALID_OUTPUT
                    for failure in exc.failures
                ):
                    self._raise_model_error(exc, "Answer writer")
                failure = exc
            except (KeyError, ValueError) as exc:
                failure = exc
            if attempt:
                LOGGER.error(
                    "Answer writer contract repair exhausted",
                    extra={"event": "answer.writer.contract_repair_exhausted"},
                )
                raise InvalidAnswerProposalError(
                    "Answer output violated finding grounding"
                ) from failure
            LOGGER.warning(
                "Answer writer contract failed; repairing once",
                extra={"event": "answer.writer.contract_repair"},
            )
            messages = [*messages, HumanMessage(content=json.dumps({
                "validation_feedback": (
                    "The prior response violated the output contract. Return valid "
                    "structured paragraphs using only the supplied F references."
                ),
            }))]
        raise AssertionError("unreachable")

    @staticmethod
    def _raise_model_error(exc: LLMError, stage: str) -> None:
        if exc.failures and all(f.kind is FailureKind.INVALID_OUTPUT for f in exc.failures):
            raise InvalidAnswerProposalError(f"{stage} returned invalid structured output") from exc
        raise AnswerSynthesisError(
            "answer_writer_unavailable", "I couldn't prepare a grounded answer right now.",
            retryable=True,
        ) from exc


def _answer_view(request: GroundedAnswerRequest):
    findings = {f"F{i}": i - 1 for i in range(1, len(request.findings) + 1)}
    evidence = {f"E{i}": item for i, item in enumerate(request.evidence, 1)}
    evidence_refs = {item.chunk_id: ref for ref, item in evidence.items()}
    return {
        "original_query": request.canonical_query,
        "findings": [{
            "ref": ref, "statement": request.findings[index].statement,
            "evidence_refs": [evidence_refs[c]
                              for c in request.findings[index].supporting_chunk_ids],
            "evidence_basis": request.findings[index].evidence_basis,
            "source_fitness": request.findings[index].source_fitness,
            "qualification": request.findings[index].qualification,
        } for ref, index in findings.items()],
        "evidence": [{
            "ref": ref, "title": item.title, "canonical_url": str(item.canonical_url),
            "heading_path": item.heading_path, "text": item.text,
        } for ref, item in evidence.items()],
    }, findings, evidence_refs
