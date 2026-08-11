import re
from html import escape

from app.features.answer_synthesis.schemas import NaturalAnswerDraft
from app.features.evidence_retrieval.schemas import EvidenceCandidate


INLINE_EVIDENCE_REF = re.compile(r"\[\[E([1-9]\d*)\]\]")
INLINE_EVIDENCE_RUN = re.compile(r"\[\[E[1-9]\d*\]\](?:\s*\[\[E[1-9]\d*\]\])*")


def render_grounded_answer(
    draft: NaturalAnswerDraft, evidence: tuple[EvidenceCandidate, ...], evidence_note: str,
) -> str:
    available = {item.chunk_id: item for item in evidence}
    references = {str(i): item for i, item in enumerate(evidence, 1)}
    cited, numbers = [], {}
    for paragraph in draft.paragraphs:
        for chunk_id in paragraph.supporting_chunk_ids:
            if chunk_id not in available:
                raise ValueError("Rendered paragraph cites unavailable evidence")
            source = available[chunk_id]
            url = str(source.canonical_url)
            if url not in numbers:
                numbers[url] = len(cited) + 1
                cited.append((source, []))
            cited[numbers[url] - 1][1].append(source)
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
                numbers[str(available[c].canonical_url)]
                for c in item.supporting_chunk_ids
            }))
        paragraphs.append(text)
    body = "\n\n".join(paragraphs) or "The approved evidence did not support a sufficiently grounded answer."
    sections = [body, f"About this answer: {evidence_note}"]
    if cited:
        entries = []
        for i, (source, chunks) in enumerate(cited, 1):
            excerpts = "\n\n".join(
                escape(chunk.text) for chunk in {item.chunk_id: item for item in chunks}.values()
            )
            entries.append(
                f'<details id="source-{i}"><summary>[{i}] '
                f'<a href="{source.canonical_url}">{escape(source.title)}</a></summary>\n\n'
                f'{excerpts}\n\n</details>'
            )
        sections.append("Sources\n\n" + "\n\n".join(entries))
    return "\n\n".join(sections)


def _anchors(numbers: list[int]) -> str:
    return "".join(f'<a href="#source-{item}">[{item}]</a>' for item in numbers)
