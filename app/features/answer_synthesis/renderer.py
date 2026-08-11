from html import escape

from app.features.answer_synthesis.schemas import NaturalAnswerDraft
from app.features.evidence_retrieval.schemas import EvidenceCandidate


def render_grounded_answer(
    draft: NaturalAnswerDraft, evidence: tuple[EvidenceCandidate, ...], evidence_note: str,
) -> str:
    available = {item.chunk_id: item for item in evidence}
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
    body = "\n\n".join(
        f"{item.text} {''.join(f'[{n}](#source-{n})' for n in dict.fromkeys(
            numbers[str(available[c].canonical_url)] for c in item.supporting_chunk_ids
        ))}"
        for item in draft.paragraphs
    ) or "The approved evidence did not support a sufficiently grounded answer."
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
