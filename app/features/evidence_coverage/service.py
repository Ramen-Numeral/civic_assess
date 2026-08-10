import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.features.evidence_coverage.errors import (
    EvidenceCoverageError,
    InvalidEvidenceCoverageProposalError,
)
from app.features.evidence_coverage.schemas import (
    EvidenceCoverageAssessment,
    EvidenceCoverageProposal,
    EvidenceCoverageRequest,
    EvidenceGap,
    RequirementFinding,
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
        try:
            if not request.evidence_view:
                return EvidenceCoverageAssessment(gaps=tuple(
                    EvidenceGap(
                        requirement_id=requirement.requirement_id,
                        description=(
                            "No local evidence addresses the requirement: "
                            f"{requirement.description}"
                        ),
                        missing_evidence=requirement.description,
                    )
                    for requirement in request.requirements
                ))
            requirements = {
                f"R{position}": requirement
                for position, requirement in enumerate(request.requirements, 1)
            }
            evidence = {
                f"E{position}": candidate
                for position, candidate in enumerate(request.evidence_view, 1)
            }
            messages = [
                SystemMessage(content=self._prompt.build()),
                HumanMessage(content=json.dumps({
                    "canonical_query": request.canonical_query,
                    "requirements": [{
                        "ref": ref,
                        "description": requirement.description,
                        "evidence_expectation": requirement.evidence_expectation,
                        "investigated_angles": list(requirement.evidence_angles),
                    } for ref, requirement in requirements.items()],
                    "evidence": [{
                        "ref": ref,
                        "title": candidate.title,
                        "canonical_url": str(candidate.canonical_url),
                        "heading_path": candidate.heading_path,
                        "text": candidate.text,
                    } for ref, candidate in evidence.items()],
                }, ensure_ascii=False)),
            ]
            proposal = await self._llm.invoke_structured(
                messages, EvidenceCoverageProposal,
            )
            assessment = _resolve_proposal(proposal, requirements, evidence)
            _validate_requirements(request, assessment)
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
        except (KeyError, ValueError) as exc:
            raise InvalidEvidenceCoverageProposalError(
                "Coverage output violated grounding invariants"
            ) from exc


def _resolve_proposal(proposal, requirements, evidence):
    return EvidenceCoverageAssessment(
        findings=tuple(RequirementFinding(
            requirement_id=requirements[item.requirement_ref].requirement_id,
            statement=item.statement,
            supporting_chunk_ids=tuple(
                evidence[ref].chunk_id for ref in item.evidence_refs
            ),
            evidence_basis=item.evidence_basis,
            source_fitness=item.source_fitness,
            qualification=item.qualification,
        ) for item in proposal.findings),
        gaps=tuple(EvidenceGap(
            requirement_id=requirements[item.requirement_ref].requirement_id,
            description=item.description,
            missing_evidence=item.missing_evidence,
        ) for item in proposal.gaps),
    )


def _validate_requirements(request, assessment) -> None:
    required = {item.requirement_id for item in request.requirements}
    represented = {
        item.requirement_id for item in (*assessment.findings, *assessment.gaps)
    }
    grounded = {item.requirement_id for item in assessment.findings}
    if represented != required:
        raise ValueError("Coverage must account for every research requirement")
    if not assessment.gaps and grounded != required:
        raise ValueError("Sufficient coverage must ground every requirement")
