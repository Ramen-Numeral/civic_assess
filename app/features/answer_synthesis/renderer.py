from app.features.answer_synthesis.schemas import GroundedAnswerDraft
from app.features.evidence_coverage.schemas import EvidenceCoverageAssessment
from app.features.evidence_retrieval.schemas import EvidenceCandidate


def render_grounded_answer(
    draft: GroundedAnswerDraft,
    coverage: EvidenceCoverageAssessment,
    evidence: tuple[EvidenceCandidate, ...],
) -> str:
    available = {item.chunk_id: item for item in evidence}
    cited = []
    numbers = {}
    for claim in draft.claims:
        for chunk_id in claim.supporting_chunk_ids:
            if chunk_id not in available:
                raise ValueError("Rendered claim cites unavailable evidence")
            if chunk_id not in numbers:
                numbers[chunk_id] = len(cited) + 1
                cited.append(available[chunk_id])

    sections = []
    if draft.claims:
        lines = ["Answer"]
        for claim in draft.claims:
            markers = "".join(
                f"[{numbers[chunk_id]}]" for chunk_id in claim.supporting_chunk_ids
            )
            lines.append(f"- {claim.text} {markers}")
        sections.append("\n".join(lines))
    else:
        sections.append(
            "The available evidence did not support a sufficiently verified "
            "answer to this question."
        )
    if coverage.gaps:
        sections.append("\n".join([
            "Limitations",
            *(
                f"- The available evidence did not establish the following: "
                f"{gap.missing_evidence}"
                for gap in coverage.gaps
            ),
        ]))
    if cited:
        sections.append("\n".join([
            "Sources",
            *(
                f"{index}. {item.title} — {item.canonical_url}"
                for index, item in enumerate(cited, 1)
            ),
        ]))
    return "\n\n".join(sections)
