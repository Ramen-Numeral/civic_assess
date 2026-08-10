import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.features.evidence_coverage.errors import (
    EvidenceCoverageError,
    InvalidEvidenceCoverageProposalError,
)
from app.features.evidence_coverage.schemas import (
    EvidenceCoverageAssessment,
    EvidenceCoverageRequest,
    EvidenceGap,
)
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.errors import FailureKind, LLMError
from app.prompts.base import Prompt


class EvidenceCoverageService:
    def __init__(self, *, llm: LLMClient, prompt: Prompt) -> None:
        self._llm = llm
        self._prompt = prompt

    async def assess(
        self,
        request: EvidenceCoverageRequest,
    ) -> EvidenceCoverageAssessment:
        evidence = request.evidence_frontier
        if not evidence:
            return EvidenceCoverageAssessment(
                sufficient=False,
                gaps=(EvidenceGap(
                    description="No local evidence is available to ground an answer.",
                    evidence_requirement=(
                        "Evidence that directly satisfies the canonical query, "
                        "rather than only providing topical background: "
                        f"{request.canonical_query}"
                    ),
                ),),
            )
        messages = [
            SystemMessage(content=self._prompt.build()),
            HumanMessage(content=json.dumps({
                "canonical_query": request.canonical_query,
                "evidence": [
                    {
                        "position": position,
                        "chunk_id": str(candidate.chunk_id),
                        "document_id": str(candidate.document_id),
                        "title": candidate.title,
                        "canonical_url": str(candidate.canonical_url),
                        "heading_path": candidate.heading_path,
                        "text": candidate.text,
                    }
                    for position, candidate in enumerate(evidence, start=1)
                ],
            }, ensure_ascii=False)),
        ]
        try:
            assessment = await self._llm.invoke_structured(
                messages,
                EvidenceCoverageAssessment,
            )
            allowed = {candidate.chunk_id for candidate in evidence}
            if any(
                chunk_id not in allowed
                for point in assessment.covered_points
                for chunk_id in point.supporting_chunk_ids
            ):
                raise ValueError("Covered point cites evidence outside the candidate set")
            return assessment
        except LLMError as exc:
            if exc.failures and all(
                failure.kind is FailureKind.INVALID_OUTPUT
                for failure in exc.failures
            ):
                raise InvalidEvidenceCoverageProposalError(
                    "Coverage model returned invalid structured output"
                ) from exc
            raise EvidenceCoverageError(
                "coverage_unavailable",
                "I couldn't assess the available evidence right now.",
                retryable=True,
            ) from exc
        except ValueError as exc:
            raise InvalidEvidenceCoverageProposalError(
                "Coverage output violated grounding invariants"
            ) from exc
