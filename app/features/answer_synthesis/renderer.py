import re
from html import escape
from uuid import UUID

from app.features.answer_synthesis.schemas import AnswerParagraph, NaturalAnswerDraft
from app.features.evidence.models import EvidenceCandidate


INLINE_EVIDENCE_REF = re.compile(r"\[\[E([1-9]\d*)\]\]")
INLINE_EVIDENCE_RUN = re.compile(r"\[\[E[1-9]\d*\]\](?:\s*\[\[E[1-9]\d*\]\])*")


def referenced_chunks(
    paragraph: AnswerParagraph, evidence: tuple[EvidenceCandidate, ...],
) -> list[UUID]:
    references = {str(i): item for i, item in enumerate(evidence, 1)}
    marked = [
        references[ref].chunk_id
        for ref in INLINE_EVIDENCE_REF.findall(paragraph.text)
        if ref in references
        and references[ref].chunk_id in paragraph.supporting_chunk_ids
    ]
    return list(dict.fromkeys(marked or paragraph.supporting_chunk_ids))


def cited_sources(
    draft: NaturalAnswerDraft, evidence: tuple[EvidenceCandidate, ...],
) -> dict[str, list[UUID]]:
    available = {item.chunk_id: item for item in evidence}
    sources: dict[str, list[UUID]] = {}
    for paragraph in draft.paragraphs:
        for chunk in referenced_chunks(paragraph, evidence):
            sources.setdefault(str(available[chunk].canonical_url), []).append(chunk)
    return sources


def render_grounded_answer(
    draft: NaturalAnswerDraft,
    evidence: tuple[EvidenceCandidate, ...],
    evidence_note: str,
    validation_warning: str | None = None,
) -> str:
    available = {item.chunk_id: item for item in evidence}
    references = {str(i): item for i, item in enumerate(evidence, 1)}
    if any(
        chunk not in available
        for paragraph in draft.paragraphs
        for chunk in paragraph.supporting_chunk_ids
    ):
        raise ValueError("Rendered paragraph cites unavailable evidence")
    sources = cited_sources(draft, evidence)
    numbers = {url: index for index, url in enumerate(sources, 1)}
    cited = [(available[chunks[0]], chunks) for chunks in sources.values()]

    paragraphs = []
    for item in draft.paragraphs:
        def citation(match, paragraph=item):
            resolved = {
                numbers[str(source.canonical_url)]
                for ref in INLINE_EVIDENCE_REF.findall(match.group())
                if (source := references.get(ref)) is not None
                and source.chunk_id in paragraph.supporting_chunk_ids
            }
            return _anchors(sorted(resolved))
        text = INLINE_EVIDENCE_RUN.sub(citation, item.text)
        if "#source-" not in text:
            text += " " + _anchors(sorted({
                numbers[str(available[chunk].canonical_url)]
                for chunk in referenced_chunks(item, evidence)
            }))
        paragraphs.append(text)

    body = "\n\n".join(paragraphs) or (
        "The approved evidence did not support a sufficiently grounded answer."
    )
    sections = []
    if validation_warning:
        sections.append(f"> **Validation warning:** {validation_warning}")
    sections.extend([body, f"About this answer: {evidence_note}"])
    if cited:
        entries = [
            f'<div id="source-{index}">[{index}] '
            f'<a href="{source.canonical_url}">{escape(source.title)}</a></div>'
            for index, (source, _) in enumerate(cited, 1)
        ]
        sections.append("Sources\n\n" + "\n".join(entries))
    return "\n\n".join(sections)


def _anchors(numbers: list[int]) -> str:
    return "".join(f'<a href="#source-{item}">[{item}]</a>' for item in numbers)
